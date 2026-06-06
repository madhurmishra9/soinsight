"""
Ingestion service: pull questions from SO Enterprise → SQLite.

IMPORTANT — field name constants (prefixed _SO_) are guesses based on typical
SO Enterprise v3 shapes. Verify each one against <SO_BASE_URL>/api/v3 Swagger
before running against a real instance. Wrong names silently default to empty/0.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.models import Question, Run
from services.so_client import SOClient

log = structlog.get_logger("soinsight.ingestion")

# ─── TODO: verify all of these against <SO_BASE_URL>/api/v3 Swagger ──────────
_SO_QUESTION_ID = "question_id"    # may be "id"
_SO_CREATION_DATE = "creation_date"  # may be "created_at" or "creation_time"
_SO_AUTHOR_KEY = "owner"           # may be "author"
_SO_AUTHOR_ID = "user_id"          # may be "account_id"
_SO_AUTHOR_ROLE = "user_type"      # may be "role" or "account_type"
_SO_HAS_ACCEPTED = "is_answered"   # may be "has_accepted_answer"
# ─────────────────────────────────────────────────────────────────────────────

DAILY_BUDGET = 9_500  # 95 % of the 10 000/day quota — remaining is headroom


class BudgetExhaustedError(RuntimeError):
    """Raised when the daily API call budget is exhausted."""


class BudgetTracker:
    """
    Simple daily call counter. Resets at midnight UTC.
    Charge once per iter_questions call (one tag fetch = one logical unit).
    """

    def __init__(self, daily_limit: int = DAILY_BUDGET) -> None:
        self._limit = daily_limit
        self._count = 0
        self._date = datetime.utcnow().date()

    def _maybe_reset(self) -> None:
        today = datetime.utcnow().date()
        if today != self._date:
            self._count = 0
            self._date = today

    def charge(self, n: int = 1) -> None:
        """Raise BudgetExhaustedError if charging n would exceed the daily limit."""
        self._maybe_reset()
        if self._count + n > self._limit:
            raise BudgetExhaustedError(
                f"Daily budget of {self._limit} calls reached ({self._count} used)"
            )
        self._count += n

    @property
    def remaining(self) -> int:
        self._maybe_reset()
        return self._limit - self._count

    @property
    def used(self) -> int:
        self._maybe_reset()
        return self._count


@dataclass
class IngestResult:
    inserted: int = 0
    skipped: int = 0
    errors: int = 0
    tags: list[str] = field(default_factory=list)


def _map_question(raw: dict[str, Any], team_slug: str | None = None) -> dict[str, Any]:
    """
    Map a raw SO Enterprise v3 question dict → Question model kwargs.

    Raises KeyError if so_id is missing (caller should catch and log).
    All other fields fall back to safe defaults when absent.
    """
    owner: dict[str, Any] = raw.get(_SO_AUTHOR_KEY) or {}
    tags_raw: list[str] = raw.get("tags") or []
    creation_ts = raw.get(_SO_CREATION_DATE, 0)
    created_at = datetime.utcfromtimestamp(creation_ts) if creation_ts else datetime.utcnow()

    return {
        "so_id": int(raw[_SO_QUESTION_ID]),
        "title": str(raw.get("title") or ""),
        "body": str(raw.get("body") or raw.get("body_markdown") or ""),
        "tags": json.dumps(tags_raw),
        "score": int(raw.get("score") or 0),
        "view_count": int(raw.get("view_count") or 0),
        "created_at": created_at,
        "author_id": int(owner.get(_SO_AUTHOR_ID) or 0),
        "author_role": str(owner[_SO_AUTHOR_ROLE]) if owner.get(_SO_AUTHOR_ROLE) else None,
        "answer_count": int(raw.get("answer_count") or 0),
        "has_accepted": bool(raw.get(_SO_HAS_ACCEPTED)),
        "team_slug": team_slug,
    }


class IngestService:
    """
    Fetches questions from SO Enterprise and upserts them into SQLite.
    Idempotent: re-running with the same products/window skips existing rows.
    """

    def __init__(
        self,
        client: SOClient,
        budget: BudgetTracker | None = None,
    ) -> None:
        self._client = client
        self._budget = budget or BudgetTracker()

    async def run(
        self,
        products: list[str],
        window_days: int,
        team: str | None,
        queue: asyncio.Queue[dict[str, Any] | None],
        engine: Engine,
    ) -> IngestResult:
        """
        Fetch questions for each tag in *products* created within *window_days*,
        upsert into SQLite, and push SSE-style progress dicts into *queue*.
        Always puts a None sentinel into *queue* when complete.
        """
        since = datetime.utcnow() - timedelta(days=window_days)
        result = IngestResult(tags=list(products))

        with Session(engine) as session:
            run_rec = Run(
                products=json.dumps(products),
                window_days=window_days,
                status="running",
            )
            session.add(run_rec)
            session.commit()
            session.refresh(run_rec)
            run_id: int | None = run_rec.id

            for tag in products:
                await queue.put({"type": "tag_start", "tag": tag})
                log.info("ingest_tag_start", tag=tag)

                try:
                    self._budget.charge()
                except BudgetExhaustedError:
                    log.warning("budget_exhausted", tag=tag)
                    await queue.put(
                        {"type": "warning", "message": "Daily budget exhausted; stopping."}
                    )
                    break

                async for raw_q in self._client.iter_questions(
                    tag=tag, since=since, team=team
                ):
                    try:
                        q_data = _map_question(raw_q, team_slug=team)
                        so_id: int = q_data["so_id"]

                        existing = session.exec(
                            select(Question).where(Question.so_id == so_id)
                        ).first()

                        if existing is None:
                            session.add(Question(**q_data))
                            result.inserted += 1
                        else:
                            result.skipped += 1

                        total = result.inserted + result.skipped
                        if total % 25 == 0:
                            session.commit()
                            await queue.put({
                                "type": "progress",
                                "tag": tag,
                                "inserted": result.inserted,
                                "skipped": result.skipped,
                            })
                    except (KeyError, ValueError, TypeError) as exc:
                        result.errors += 1
                        log.warning("map_question_failed", error=str(exc))

            session.commit()

            if run_id is not None:
                run_rec_upd = session.get(Run, run_id)
                if run_rec_upd is not None:
                    run_rec_upd.finished_at = datetime.utcnow()
                    run_rec_upd.status = "done" if result.errors == 0 else "partial"
                    run_rec_upd.counts = json.dumps({
                        "inserted": result.inserted,
                        "skipped": result.skipped,
                        "errors": result.errors,
                    })
                    session.add(run_rec_upd)
                    session.commit()

        log.info(
            "ingest_done",
            inserted=result.inserted,
            skipped=result.skipped,
            errors=result.errors,
        )
        await queue.put({
            "type": "done",
            "inserted": result.inserted,
            "skipped": result.skipped,
            "errors": result.errors,
        })
        await queue.put(None)
        return result
