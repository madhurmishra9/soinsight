from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.dates import resolve_range
from app.db import engine as app_engine
from app.models import Classification, Question
from services.aggregator import AggregatorService
from services.classifier import ClassifierService

log = structlog.get_logger("soinsight.routers.analysis")

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

# Maps run_id → SSE event queue
_run_queues: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}


class AnalysisRequest(BaseModel):
    products: list[str]
    window_days: int = 30
    from_date: str | None = None
    to_date: str | None = None


class AnalysisResponse(BaseModel):
    run_id: str
    status: str


async def _run_analysis(
    run_id: str,
    products: list[str],
    window_days: int,
    queue: asyncio.Queue[dict[str, Any] | None],
    from_date: str | None = None,
    to_date: str | None = None,
) -> None:
    try:
        since, until = resolve_range(window_days, from_date, to_date)
        wanted = {p.lower() for p in products}
        with Session(app_engine) as session:
            rows = session.exec(
                select(Question).where(
                    Question.created_at >= since, Question.created_at <= until
                )
            ).all()

        def _names(q: Question) -> set[str]:
            try:
                raw = json.loads(q.tags or "[]")
            except Exception:
                return set()
            out: set[str] = set()
            for t in raw:
                out.add((t["name"] if isinstance(t, dict) else str(t)).lower())
            return out

        to_classify = [q for q in rows if wanted & _names(q)]

        # Pre-filter: skip questions that already have a classification row.
        # The classifier is idempotent but does a DB round-trip per question;
        # filtering here avoids those round-trips for already-classified questions.
        candidate_ids = [q.id for q in to_classify if q.id is not None]
        already_classified: set[int] = set()
        if candidate_ids:
            with Session(app_engine) as chk:
                already_classified = set(
                    chk.exec(
                        select(Classification.question_id).where(
                            Classification.question_id.in_(candidate_ids)  # type: ignore[arg-type]
                        )
                    ).all()
                )
        unclassified = [q for q in to_classify if q.id not in already_classified]
        skipped_cls = len(to_classify) - len(unclassified)
        msg = f"Classifying {len(unclassified)} new questions"
        if skipped_cls:
            msg += f" ({skipped_cls} already classified — skipping)"
        await queue.put({"type": "info", "message": msg})
        log.info("analysis_classification_started", run_id=run_id,
                 new=len(unclassified), already_classified=skipped_cls)

        classifier = ClassifierService()
        await classifier.classify_questions(unclassified, engine=app_engine)

        svc = AggregatorService()
        await svc.run(
            products=products,
            window_days=window_days,
            engine=app_engine,
            queue=queue,
            from_date=from_date,
            to_date=to_date,
        )
    except Exception as exc:
        log.error("analysis_background_error", run_id=run_id, error=str(exc))
        await queue.put({"type": "error", "message": str(exc)})
        await queue.put(None)


@router.post("/start", response_model=AnalysisResponse)
async def start_analysis(
    body: AnalysisRequest,
    background_tasks: BackgroundTasks,
) -> AnalysisResponse:
    """Start a background aggregation run; stream progress from GET /api/analysis/stream."""
    if not body.products:
        raise HTTPException(status_code=422, detail="products list must not be empty")

    run_id = uuid.uuid4().hex
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    _run_queues[run_id] = queue

    background_tasks.add_task(
        _run_analysis, run_id, body.products, body.window_days, queue,
        body.from_date, body.to_date,
    )
    log.info("analysis_started", run_id=run_id, products=body.products)
    return AnalysisResponse(run_id=run_id, status="started")


@router.get("/stream")
async def stream_analysis(run_id: str) -> StreamingResponse:
    """SSE stream of aggregation progress events for the given run_id."""
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
            # (The /questions and /remediation streams already do this.)
            _run_queues.pop(run_id, None)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
