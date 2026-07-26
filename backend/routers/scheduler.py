from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.dates import utcnow
from app.db import engine as app_engine
from app.models import ScheduleConfig

log = structlog.get_logger("soinsight.routers.scheduler")

router = APIRouter(prefix="/api/schedule", tags=["schedule"])

# Injected by main.py after the SchedulerService is started.
_scheduler: Any = None

# Strong references to in-flight manual-trigger tasks. The event loop only holds
# weak references to tasks, so a bare `asyncio.create_task(...)` whose result is
# discarded can be garbage-collected mid-run; keeping the reference until the
# task completes is what keeps a triggered refresh alive to the end.
_trigger_tasks: set[asyncio.Task[None]] = set()


def set_scheduler(svc: Any) -> None:
    global _scheduler
    _scheduler = svc


def _spawn_trigger(coro: Any) -> asyncio.Task[None]:
    """Run *coro* in the background, holding a reference and surfacing errors."""
    task: asyncio.Task[None] = asyncio.create_task(coro)
    _trigger_tasks.add(task)
    task.add_done_callback(_trigger_tasks.discard)

    def _log_failure(t: asyncio.Task[None]) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            # Without this the exception is only surfaced as an unretrieved-task
            # warning at GC time, long after the trigger appeared to succeed.
            log.error("schedule_trigger_failed", error=str(exc), exc_info=exc)

    task.add_done_callback(_log_failure)
    return task


# ── Request / response models ─────────────────────────────────────────────────

class ScheduleConfigRequest(BaseModel):
    enabled: bool = False
    interval_hours: int = Field(default=24, ge=1, le=8760)
    products: list[str] = []
    window_days: int = Field(default=30, ge=1, le=365)


class ScheduleConfigResponse(BaseModel):
    enabled: bool
    interval_hours: int
    products: list[str]
    window_days: int
    last_run_at: datetime | None
    next_run_at: datetime | None


class ScheduleStatusResponse(BaseModel):
    enabled: bool
    running: bool
    last_run_at: datetime | None
    next_run_at: datetime | None


_DEFAULTS = ScheduleConfigResponse(
    enabled=False,
    interval_hours=24,
    products=[],
    window_days=30,
    last_run_at=None,
    next_run_at=None,
)


def _to_response(cfg: ScheduleConfig) -> ScheduleConfigResponse:
    return ScheduleConfigResponse(
        enabled=cfg.enabled,
        interval_hours=cfg.interval_hours,
        products=json.loads(cfg.products or "[]"),
        window_days=cfg.window_days,
        last_run_at=cfg.last_run_at,
        next_run_at=cfg.next_run_at,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=ScheduleConfigResponse)
async def get_schedule() -> ScheduleConfigResponse:
    """Return the current schedule configuration (or defaults if not yet set)."""
    with Session(app_engine) as session:
        cfg = session.exec(select(ScheduleConfig)).first()
    return _to_response(cfg) if cfg is not None else _DEFAULTS


@router.post("", response_model=ScheduleConfigResponse)
async def set_schedule(body: ScheduleConfigRequest) -> ScheduleConfigResponse:
    """Create or replace the schedule configuration."""
    with Session(app_engine) as session:
        cfg = session.exec(select(ScheduleConfig)).first()
        if cfg is None:
            cfg = ScheduleConfig()
            session.add(cfg)

        cfg.enabled = body.enabled
        cfg.interval_hours = body.interval_hours
        cfg.products = json.dumps(body.products)
        cfg.window_days = body.window_days

        if body.enabled and cfg.next_run_at is None:
            cfg.next_run_at = utcnow() + timedelta(hours=body.interval_hours)
        if not body.enabled:
            cfg.next_run_at = None

        session.commit()
        session.refresh(cfg)
        result = _to_response(cfg)

    log.info(
        "schedule_updated",
        enabled=body.enabled,
        interval_hours=body.interval_hours,
        products=body.products,
    )
    return result


@router.post("/trigger", status_code=202)
async def trigger_now() -> dict[str, str]:
    """Immediately fire a full refresh, ignoring the configured interval."""
    with Session(app_engine) as session:
        cfg = session.exec(select(ScheduleConfig)).first()

    if cfg is None or not json.loads(cfg.products or "[]"):
        raise HTTPException(
            status_code=400,
            detail="No products configured. POST /api/schedule first.",
        )
    if _scheduler is None:
        raise HTTPException(status_code=503, detail="Scheduler service not initialised.")

    _spawn_trigger(_scheduler.trigger_now())
    return {"status": "triggered"}


@router.get("/status", response_model=ScheduleStatusResponse)
async def get_status() -> ScheduleStatusResponse:
    """Return runtime status: enabled, currently running, last/next run times."""
    with Session(app_engine) as session:
        cfg = session.exec(select(ScheduleConfig)).first()
    return ScheduleStatusResponse(
        enabled=cfg.enabled if cfg is not None else False,
        running=_scheduler.is_running() if _scheduler is not None else False,
        last_run_at=cfg.last_run_at if cfg is not None else None,
        next_run_at=cfg.next_run_at if cfg is not None else None,
    )
