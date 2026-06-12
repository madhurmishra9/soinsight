"""
In-process async scheduler for SOInsight.

A background asyncio task polls the schedule_config table every _POLL_INTERVAL
seconds.  When a run is due (enabled=True, next_run_at ≤ now), it sequentially
runs:  ingest → classify unclassified questions → aggregate.

All three steps are idempotent:
  - Ingest deduplicates by so_id.
  - ClassifierService skips already-classified questions internally.
  - AggregatorService upserts patterns keyed by (product_tag, window_days,
    main_category, sub_category).

_POLL_INTERVAL is a module-level constant so tests can monkeypatch it to 0.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.models import Classification, Question, ScheduleConfig
from app.settings import settings
from services.aggregator import AggregatorService
from services.classifier import ClassifierService
from services.ingestion import IngestService
from services.so_client import SOAuth, SOClient

log = structlog.get_logger("soinsight.scheduler")

# How often the loop checks for config changes or a due run.
# Monkeypatch this to 0 in tests that exercise the loop directly.
_POLL_INTERVAL: float = 60.0


class SchedulerService:
    """
    Lifecycle: call start() in FastAPI lifespan; stop() on shutdown.
    trigger_now() fires a run immediately regardless of timing.
    """

    def __init__(self, engine: Engine, ollama_url: str) -> None:
        self._engine = engine
        self._ollama_url = ollama_url
        self._task: asyncio.Task[None] | None = None
        self._currently_running = False

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="scheduler_loop")
        log.info("scheduler_started", poll_interval=_POLL_INTERVAL)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        log.info("scheduler_stopped")

    def is_running(self) -> bool:
        return self._currently_running

    # ── Manual trigger ─────────────────────────────────────────────────────────

    async def trigger_now(self) -> None:
        """Fire a full refresh immediately, ignoring the configured interval."""
        config = self._read_config()
        if config is None:
            raise ValueError("No schedule configured.")
        products = json.loads(config.products or "[]")
        if not products:
            raise ValueError("No products configured for the schedule.")
        await self._execute_run(config)

    # ── Scheduler loop ─────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while True:
            try:
                config = self._read_config()
                if config is None or not config.enabled:
                    await asyncio.sleep(_POLL_INTERVAL)
                    continue

                products = json.loads(config.products or "[]")
                if not products:
                    await asyncio.sleep(_POLL_INTERVAL)
                    continue

                now = datetime.utcnow()
                if config.next_run_at is None or now >= config.next_run_at:
                    await self._execute_run(config)
                else:
                    secs_until = (config.next_run_at - now).total_seconds()
                    await asyncio.sleep(min(_POLL_INTERVAL, max(1.0, secs_until)))

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("scheduler_loop_error", error=str(exc))
                await asyncio.sleep(_POLL_INTERVAL)

    # ── DB helpers ─────────────────────────────────────────────────────────────

    def _read_config(self) -> ScheduleConfig | None:
        with Session(self._engine) as session:
            return session.exec(select(ScheduleConfig)).first()

    def _get_unclassified(self) -> list[Question]:
        """Return questions that do not yet have a classification record."""
        with Session(self._engine) as session:
            classified_ids: set[int] = set(
                session.exec(select(Classification.question_id)).all()
            )
            all_questions: list[Question] = list(session.exec(select(Question)).all())
            # Filter inside the session while all attributes are still loaded.
            return [q for q in all_questions if q.id not in classified_ids]

    # ── Full refresh pipeline ──────────────────────────────────────────────────

    async def _execute_run(self, config: ScheduleConfig) -> None:
        if self._currently_running:
            log.warning("scheduled_run_skipped", reason="another run already in progress")
            return

        self._currently_running = True
        products = json.loads(config.products or "[]")
        log.info("scheduled_run_start", products=products, window_days=config.window_days)

        # Stamp timestamps before the run so next_run_at is set even on partial failure.
        now = datetime.utcnow()
        with Session(self._engine) as session:
            cfg = session.get(ScheduleConfig, config.id)
            if cfg is not None:
                cfg.last_run_at = now
                cfg.next_run_at = now + timedelta(hours=config.interval_hours)
                session.add(cfg)
                session.commit()

        try:
            await self._ingest(products, config.window_days)
            await self._classify()
            await self._aggregate(products, config.window_days)
        except Exception as exc:
            log.error("scheduled_run_error", error=str(exc))
        finally:
            self._currently_running = False
            log.info("scheduled_run_done", products=products)

    async def _ingest(self, products: list[str], window_days: int) -> None:
        # Prefer config set via the Settings UI; fall back to env vars.
        from routers.settings import _current_config  # noqa: PLC0415

        base_url = _current_config.get("base_url") or settings.so_base_url
        api_key = _current_config.get("api_key") or settings.so_api_key
        team: str | None = _current_config.get("team") or settings.so_team or None

        auth = SOAuth(mode="bearer", api_key=api_key or None)
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        try:
            async with SOClient(base_url=base_url, auth=auth) as client:
                svc = IngestService(client=client)
                await svc.run(
                    products=products,
                    window_days=window_days,
                    team=team,
                    queue=queue,
                    engine=self._engine,
                )
        except Exception as exc:
            log.error("scheduled_ingest_error", error=str(exc))
        # Drain the queue so the coroutine can be GC-ed cleanly.
        while not queue.empty():
            queue.get_nowait()

    async def _classify(self) -> None:
        unclassified = self._get_unclassified()
        if not unclassified:
            log.info("scheduled_classify_skip", reason="all questions already classified")
            return
        log.info("scheduled_classify_start", count=len(unclassified))
        try:
            svc = ClassifierService(ollama_url=self._ollama_url)
            await svc.classify_questions(unclassified, self._engine)
        except Exception as exc:
            log.error("scheduled_classify_error", error=str(exc))

    async def _aggregate(self, products: list[str], window_days: int) -> None:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        try:
            svc = AggregatorService()
            await svc.run(
                products=products,
                window_days=window_days,
                engine=self._engine,
                queue=queue,
            )
        except Exception as exc:
            log.error("scheduled_aggregate_error", error=str(exc))
        while not queue.empty():
            queue.get_nowait()
