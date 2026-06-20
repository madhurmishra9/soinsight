"""Pattern dismissals — analysts snooze handled (product, main, sub) clusters."""

from __future__ import annotations

from datetime import datetime, timedelta

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.dates import utcnow
from app.db import get_session
from app.models import PatternDismissal

log = structlog.get_logger("soinsight.routers.dismissals")

router = APIRouter(prefix="/api/patterns/dismiss", tags=["patterns"])


class DismissRequest(BaseModel):
    product: str
    main: str
    sub: str
    # Either a duration (preferred for "snooze for N days") or an explicit until.
    days: int | None = Field(default=None, ge=1, le=3650)
    until: datetime | None = None
    reason: str | None = None


class DismissedItem(BaseModel):
    id: int
    product: str
    main: str
    sub: str
    dismissed_until: datetime | None
    reason: str | None
    created_at: datetime


def _to_item(row: PatternDismissal) -> DismissedItem:
    return DismissedItem(
        id=row.id or 0,
        product=row.product_tag,
        main=row.main_category,
        sub=row.sub_category,
        dismissed_until=row.dismissed_until,
        reason=row.reason,
        created_at=row.created_at,
    )


def _find_existing(
    session: Session, product: str, main: str, sub: str
) -> PatternDismissal | None:
    return session.exec(
        select(PatternDismissal).where(
            PatternDismissal.product_tag == product,
            PatternDismissal.main_category == main,
            PatternDismissal.sub_category == sub,
        )
    ).first()


def active_dismissed_keys(
    session: Session, product: str, now: datetime | None = None
) -> set[tuple[str, str]]:
    """Return the (main, sub) tuples currently dismissed for *product*.

    Filters out expired dismissals (dismissed_until in the past). A row with
    no dismissed_until is treated as indefinite.
    """
    cutoff = now or utcnow()
    rows = session.exec(
        select(PatternDismissal).where(PatternDismissal.product_tag == product)
    ).all()
    return {
        (r.main_category, r.sub_category)
        for r in rows
        if r.dismissed_until is None or r.dismissed_until > cutoff
    }


@router.post("", response_model=DismissedItem)
def dismiss_pattern(
    body: DismissRequest,
    session: Session = Depends(get_session),
) -> DismissedItem:
    """Snooze a (product, main, sub) pattern for *days* (or indefinitely)."""
    if body.days is not None and body.until is not None:
        raise HTTPException(status_code=422, detail="Send `days` or `until`, not both.")
    dismissed_until: datetime | None = body.until
    if body.days is not None:
        dismissed_until = utcnow() + timedelta(days=body.days)

    existing = _find_existing(session, body.product, body.main, body.sub)
    if existing is None:
        row = PatternDismissal(
            product_tag=body.product,
            main_category=body.main,
            sub_category=body.sub,
            dismissed_until=dismissed_until,
            reason=body.reason,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        log.info("pattern_dismissed", product=body.product, main=body.main, sub=body.sub)
        return _to_item(row)

    existing.dismissed_until = dismissed_until
    existing.reason = body.reason if body.reason is not None else existing.reason
    session.add(existing)
    session.commit()
    session.refresh(existing)
    log.info("pattern_dismissal_updated", id=existing.id)
    return _to_item(existing)


@router.delete("", status_code=204)
def restore_pattern(
    product: str = Query(...),
    main: str = Query(...),
    sub: str = Query(...),
    session: Session = Depends(get_session),
) -> None:
    """Cancel an active dismissal. No-op if none exists (still 204)."""
    existing = _find_existing(session, product, main, sub)
    if existing is None:
        return
    session.delete(existing)
    session.commit()
    log.info("pattern_dismissal_restored", product=product, main=main, sub=sub)


@router.get("", response_model=list[DismissedItem])
def list_dismissed(
    product: str | None = Query(None),
    include_expired: bool = Query(False),
    session: Session = Depends(get_session),
) -> list[DismissedItem]:
    """Active dismissals, optionally filtered by product."""
    stmt = select(PatternDismissal)
    if product is not None:
        stmt = stmt.where(PatternDismissal.product_tag == product)
    rows = session.exec(stmt).all()
    now = utcnow()
    if not include_expired:
        rows = [r for r in rows if r.dismissed_until is None or r.dismissed_until > now]
    rows = sorted(rows, key=lambda r: r.created_at, reverse=True)
    return [_to_item(r) for r in rows]
