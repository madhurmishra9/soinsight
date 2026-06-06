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


class FetchResponse(BaseModel):
    run_id: str
    status: str


async def _run_ingestion(
    run_id: str,
    products: list[str],
    window_days: int,
    queue: asyncio.Queue[dict[str, Any] | None],
) -> None:
    """Background coroutine: create an SO client and run ingestion."""
    base_url = _current_config.get("so_base_url") or settings.so_base_url
    api_key = _current_config.get("so_api_key") or settings.so_api_key
    team: str | None = _current_config.get("so_team") or settings.so_team or None

    auth = SOAuth(mode="api_key", api_key=api_key or None)
    try:
        async with SOClient(base_url=base_url, auth=auth) as client:
            service = IngestService(client=client, budget=_budget)
            await service.run(
                products=products,
                window_days=window_days,
                team=team,
                queue=queue,
                engine=app_engine,
            )
    except Exception as exc:
        log.error("ingest_background_error", run_id=run_id, error=str(exc))
        await queue.put({"type": "error", "message": str(exc)})
        await queue.put(None)


@router.post("", response_model=FetchResponse)
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
        _run_ingestion, run_id, body.products, body.window_days, queue
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
        while True:
            event = await queue.get()
            if event is None:
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
