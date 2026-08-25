from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta
from typing import Any, Literal

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.dates import utcnow
from app.db import engine as app_engine
from app.db import get_session
from app.models import Answer, Question, Run
from app.settings import settings
from routers.settings import _current_config
from services.ingestion import BudgetTracker, IngestService
from services.so_client import SOAuth, SOClient

log = structlog.get_logger("soinsight.routers.questions")

router = APIRouter(prefix="/api/questions", tags=["questions"])

# One budget tracker shared across all runs within this process lifetime.
_budget = BudgetTracker()

# Maps run_id → SSE event queue. Events are dicts; None is the done sentinel.
_run_queues: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}


class FetchRequest(BaseModel):
    products: list[str]
    window_days: int = 30
    from_date: str | None = None
    to_date: str | None = None
    incremental: bool = True


class FetchResponse(BaseModel):
    run_id: str
    status: str


class TagCoverage(BaseModel):
    """How much data is already stored locally for one tag (read live from DB)."""
    tag: str
    question_count: int = 0
    answer_count: int = 0
    earliest_question_at: datetime | None = None
    latest_question_at: datetime | None = None   # the "data fetched till" watermark
    last_fetch_at: datetime | None = None         # when a fetch last ran for this tag


@router.get("/coverage", response_model=list[TagCoverage])
def coverage(
    products: str = Query("", description="Comma-separated tags to report coverage for"),
    session: Session = Depends(get_session),
) -> list[TagCoverage]:
    """Per-tag local-data coverage, computed directly from the database.

    `latest_question_at` is the newest stored question's creation date for the
    tag — i.e. the point up to which local data is current, and exactly the
    watermark an incremental fetch resumes from.
    """
    tags = [t.strip() for t in products.split(",") if t.strip()]
    out: list[TagCoverage] = []
    for tag in tags:
        like = f'%"{tag}"%'
        q_filter = col(Question.tags).like(like)
        q_count = session.exec(select(func.count()).select_from(Question).where(q_filter)).one()
        latest = session.exec(select(func.max(Question.created_at)).where(q_filter)).first()
        earliest = session.exec(select(func.min(Question.created_at)).where(q_filter)).first()
        so_ids_subq = select(Question.so_id).where(q_filter)
        a_count = session.exec(
            select(func.count()).select_from(Answer).where(
                col(Answer.question_so_id).in_(so_ids_subq)
            )
        ).one()
        last_fetch = session.exec(
            select(func.max(Run.finished_at)).where(
                col(Run.products).like(like), col(Run.finished_at).is_not(None)
            )
        ).first()
        out.append(TagCoverage(
            tag=tag,
            question_count=int(q_count or 0),
            answer_count=int(a_count or 0),
            earliest_question_at=earliest,
            latest_question_at=latest,
            last_fetch_at=last_fetch,
        ))
    return out


# ─── Tag availability validation ──────────────────────────────────────────────
#
# A typed tag is validated against the instance's real tag list. The list is
# cached per-team with a short TTL so typing doesn't hammer SO. When the list
# can't be fetched (SO unreachable, no key, endpoint unsupported) the status is
# "unknown" — never "unavailable" — so a genuinely valid tag is never shown red.

_TAG_TTL = timedelta(minutes=10)
_TAG_FETCH_CAP = 20000
# "<base_url>|<team-slug>" → {"tags": dict[str, int], "at": datetime, "ok": bool}
#
# The instance URL is part of the key, not just the team: pointing Settings at a
# different instance must not keep validating tags against the previous one for
# the rest of the TTL.
_tag_index_cache: dict[str, dict[str, Any]] = {}


class TagValidation(BaseModel):
    tag: str
    status: Literal["available", "unavailable", "unknown"]
    question_count: int | None = None


def tag_cache_key(base_url: str, team: str | None) -> str:
    """Cache key for one instance+scope pair. Exported so callers and tests
    build it the same way instead of duplicating the format."""
    return f"{base_url}|{team or ''}"


async def _load_tag_index(team: str | None, force: bool = False) -> dict[str, Any]:
    base_url = _current_config.get("base_url") or settings.so_base_url
    key = tag_cache_key(base_url, team)
    cached = _tag_index_cache.get(key)
    if (
        not force
        and cached
        and cached["ok"]
        and utcnow() - cached["at"] < _TAG_TTL
    ):
        return cached

    api_key = _current_config.get("api_key") or settings.so_api_key
    auth = SOAuth(mode="bearer", api_key=api_key or None)

    names: dict[str, int] = {}
    ok = False
    try:
        async with SOClient(base_url=base_url, auth=auth) as client:
            async for t in client.list_tags(team=team):
                name = str(t.get("name") or "").strip().lower()
                if not name:
                    continue
                # TODO: confirm the count field name in Swagger.
                count = t.get("questionCount") or t.get("count") or t.get("question_count") or 0
                names[name] = int(count or 0)
                if len(names) >= _TAG_FETCH_CAP:
                    break
        ok = True
    except Exception as exc:  # unreachable / unauthorised / unsupported
        log.warning("tag_index_fetch_failed", error=str(exc))
        ok = False

    if ok:
        entry = {"tags": names, "at": utcnow(), "ok": True}
        _tag_index_cache[key] = entry
        return entry
    # On failure, fall back to a stale cache if we have one; otherwise report not-ok.
    return cached or {"tags": {}, "at": utcnow(), "ok": False}


class AvailableTag(BaseModel):
    tag: str
    question_count: int


class AvailableTagsResponse(BaseModel):
    ok: bool                 # False when the instance's tag list could not be fetched
    tags: list[AvailableTag]
    total: int                # total tags in the index, before the `search` filter


@router.get("/available-tags", response_model=AvailableTagsResponse)
async def available_tags(
    search: str = Query("", description="Case-insensitive substring filter on tag name"),
    limit: int = Query(1000, ge=1, le=20000, description="Max tags to return, most-used first"),
    refresh: bool = Query(False, description="Force-refresh the cached tag list from SO"),
) -> AvailableTagsResponse:
    """All tags known on the configured Stack Overflow instance, for the Fetch page's
    tag picker — populated as soon as a connection is established (this primes the
    same cached index used by /validate-tags), so users pick from real tags instead
    of typing them blind.
    """
    team: str | None = _current_config.get("team") or settings.so_team or None
    index = await _load_tag_index(team, force=refresh)

    names = index["tags"]
    needle = search.strip().lower()
    items = [(n, c) for n, c in names.items() if not needle or needle in n]
    items.sort(key=lambda x: x[1], reverse=True)
    items = items[:limit]

    return AvailableTagsResponse(
        ok=index["ok"],
        tags=[AvailableTag(tag=n, question_count=c) for n, c in items],
        total=len(names),
    )


@router.get("/validate-tags", response_model=list[TagValidation])
async def validate_tags(
    tags: str = Query("", description="Comma-separated tags to validate against the instance"),
    refresh: bool = Query(False, description="Force-refresh the cached tag list"),
) -> list[TagValidation]:
    """Report whether each typed tag exists on the configured SO instance."""
    wanted = [t.strip() for t in tags.split(",") if t.strip()]
    if not wanted:
        return []

    team: str | None = _current_config.get("team") or settings.so_team or None
    index = await _load_tag_index(team, force=refresh)

    out: list[TagValidation] = []
    for t in wanted:
        if not index["ok"]:
            out.append(TagValidation(tag=t, status="unknown"))
            continue
        match = index["tags"].get(t.lower())
        if match is not None:
            out.append(TagValidation(tag=t, status="available", question_count=match))
        else:
            out.append(TagValidation(tag=t, status="unavailable"))
    return out


async def _run_ingestion(
    run_id: str,
    products: list[str],
    window_days: int,
    queue: asyncio.Queue[dict[str, Any] | None],
    from_date: str | None = None,
    to_date: str | None = None,
    incremental: bool = True,
) -> None:
    """Background coroutine: create an SO client and run ingestion."""
    base_url = _current_config.get("base_url") or settings.so_base_url
    api_key = _current_config.get("api_key") or settings.so_api_key
    team: str | None = _current_config.get("team") or settings.so_team or None

    auth = SOAuth(mode="bearer", api_key=api_key or None)
    try:
        async with SOClient(base_url=base_url, auth=auth) as client:
            service = IngestService(
                client=client,
                budget=_budget,
                fetch_answers=settings.fetch_answers,
                answer_concurrency=settings.answer_fetch_concurrency,
            )
            await service.run(
                products=products,
                window_days=window_days,
                team=team,
                queue=queue,
                engine=app_engine,
                from_date=from_date,
                to_date=to_date,
                incremental=incremental,
            )
    except Exception as exc:
        log.error("ingest_background_error", run_id=run_id, error=str(exc))
        await queue.put({"type": "error", "message": str(exc)})
        await queue.put(None)


@router.post("/fetch", response_model=FetchResponse)
async def fetch_questions(
    body: FetchRequest,
    background_tasks: BackgroundTasks,
) -> FetchResponse:
    """Start a background ingestion run; stream progress from GET /api/questions/stream."""
    if not body.products:
        raise HTTPException(status_code=422, detail="products list must not be empty")

    run_id = uuid.uuid4().hex
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    _run_queues[run_id] = queue

    background_tasks.add_task(
        _run_ingestion, run_id, body.products, body.window_days, queue,
        body.from_date, body.to_date, body.incremental,
    )
    log.info("ingest_started", run_id=run_id, products=body.products)
    return FetchResponse(run_id=run_id, status="started")


@router.get("/stream")
async def stream_progress(run_id: str) -> StreamingResponse:
    """SSE stream of ingestion progress events for the given run_id."""
    queue = _run_queues.get(run_id)
    if queue is None:
        raise HTTPException(status_code=404, detail=f"run_id {run_id!r} not found")

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            while True:
                event = await queue.get()
                if event is None:
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    break
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            # Drop the queue whether the stream completed normally or the client
            # disconnected; otherwise _run_queues grows unbounded over time.
            _run_queues.pop(run_id, None)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
