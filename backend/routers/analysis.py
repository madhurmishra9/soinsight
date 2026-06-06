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

from app.db import engine as app_engine
from services.aggregator import AggregatorService

log = structlog.get_logger("soinsight.routers.analysis")

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

# Maps run_id → SSE event queue
_run_queues: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}


class AnalysisRequest(BaseModel):
    products: list[str]
    window_days: int = 30


class AnalysisResponse(BaseModel):
    run_id: str
    status: str


async def _run_analysis(
    run_id: str,
    products: list[str],
    window_days: int,
    queue: asyncio.Queue[dict[str, Any] | None],
) -> None:
    try:
        svc = AggregatorService()
        await svc.run(products=products, window_days=window_days, engine=app_engine, queue=queue)
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
        _run_analysis, run_id, body.products, body.window_days, queue
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
        while True:
            event = await queue.get()
            if event is None:
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
