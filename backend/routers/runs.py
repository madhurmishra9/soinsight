"""Run history — surface stored ingest/aggregate runs to the UI."""

from __future__ import annotations

import json
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, col, desc, select

from app.db import get_session
from app.models import Run

log = structlog.get_logger("soinsight.routers.runs")

router = APIRouter(prefix="/api/runs", tags=["runs"])


class RunItem(BaseModel):
    id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    products: list[str] = Field(default_factory=list)
    window_days: int
    duration_seconds: float | None = None
    counts: dict[str, int] = Field(default_factory=dict)


def _to_item(row: Run) -> RunItem:
    try:
        products = json.loads(row.products or "[]")
        if not isinstance(products, list):
            products = []
    except (json.JSONDecodeError, ValueError, TypeError):
        products = []
    try:
        counts = json.loads(row.counts or "{}")
        if not isinstance(counts, dict):
            counts = {}
    except (json.JSONDecodeError, ValueError, TypeError):
        counts = {}

    duration: float | None = None
    if row.finished_at and row.started_at:
        duration = (row.finished_at - row.started_at).total_seconds()

    return RunItem(
        id=row.id or 0,
        started_at=row.started_at,
        finished_at=row.finished_at,
        status=row.status,
        products=products,
        window_days=row.window_days,
        duration_seconds=duration,
        counts={k: int(v) for k, v in counts.items() if isinstance(v, (int, float))},
    )


@router.get("", response_model=list[RunItem])
def list_runs(
    limit: int = Query(50, ge=1, le=500, description="Max rows to return"),
    offset: int = Query(0, ge=0, description="Skip this many newest rows"),
    status: str | None = Query(None, description="Filter to one status (running/done/partial/failed)"),
    session: Session = Depends(get_session),
) -> list[RunItem]:
    """Return runs newest-first. Always returns a list — empty if no runs yet."""
    stmt = select(Run)
    if status:
        stmt = stmt.where(Run.status == status)
    stmt = stmt.order_by(desc(col(Run.started_at))).offset(offset).limit(limit)
    rows = session.exec(stmt).all()
    return [_to_item(r) for r in rows]
