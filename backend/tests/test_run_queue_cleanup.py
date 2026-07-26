"""Regression tests for B2 — SSE _run_queues must not leak run_ids."""

from __future__ import annotations

import asyncio

from routers import analysis as a_router
from routers import questions as q_router
from routers import remediation as r_router


async def _drain(stream_resp) -> None:  # type: ignore[no-untyped-def]
    # StreamingResponse.body_iterator is the underlying async generator.
    async for _ in stream_resp.body_iterator:
        pass


async def test_questions_stream_removes_queue_when_done() -> None:
    run_id = "test-questions-run"
    queue: asyncio.Queue = asyncio.Queue()
    q_router._run_queues[run_id] = queue
    await queue.put({"type": "tag_start", "tag": "x"})
    await queue.put(None)  # done sentinel

    resp = await q_router.stream_progress(run_id=run_id)
    await _drain(resp)

    assert run_id not in q_router._run_queues, "queue must be removed after stream"


async def test_remediation_stream_removes_queue_when_done() -> None:
    run_id = "test-rem-run"
    queue: asyncio.Queue = asyncio.Queue()
    r_router._run_queues[run_id] = queue
    await queue.put({"type": "info", "message": "hi"})
    await queue.put(None)

    resp = await r_router.stream_remediation(run_id=run_id)
    await _drain(resp)

    assert run_id not in r_router._run_queues


async def test_analysis_stream_removes_queue_when_done() -> None:
    """The analysis stream was the one router still missing this cleanup."""
    run_id = "test-analysis-run"
    queue: asyncio.Queue = asyncio.Queue()
    a_router._run_queues[run_id] = queue
    await queue.put({"type": "tag_start", "tag": "x"})
    await queue.put(None)

    resp = await a_router.stream_analysis(run_id=run_id)
    await _drain(resp)

    assert run_id not in a_router._run_queues


async def test_analysis_stream_removes_queue_on_client_disconnect() -> None:
    """An abandoned stream (client hung up mid-run) must not leak either."""
    run_id = "test-analysis-abandoned"
    queue: asyncio.Queue = asyncio.Queue()
    a_router._run_queues[run_id] = queue
    await queue.put({"type": "tag_start", "tag": "x"})

    resp = await a_router.stream_analysis(run_id=run_id)
    gen = resp.body_iterator
    await gen.__anext__()          # consume one event, then walk away
    await gen.aclose()             # what Starlette does on disconnect

    assert run_id not in a_router._run_queues
