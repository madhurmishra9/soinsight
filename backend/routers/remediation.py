from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.db import engine as app_engine
from app.db import get_session
from app.models import Answer, Question, Remediation
from routers.insights import _question_url
from services.remediation import RemediationService

log = structlog.get_logger("soinsight.routers.remediation")

router = APIRouter(prefix="/api/remediation", tags=["remediation"])

# Maps run_id → SSE event queue
_run_queues: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}


# ─── Schemas ──────────────────────────────────────────────────────────────────

class EvidenceQuestion(BaseModel):
    so_id: int
    title: str
    url: str | None = None


class EvidenceAnswer(BaseModel):
    so_id: int
    question_so_id: int
    snippet: str
    is_accepted: bool
    score: int


class RemediationItem(BaseModel):
    main_category: str
    sub_category: str
    question_count: int
    distinct_users: int
    root_cause: str
    solution: str
    prevention: str
    confidence: float
    grounded: bool
    model: str
    generated_at: datetime
    evidence_questions: list[EvidenceQuestion] = Field(default_factory=list)
    evidence_answers: list[EvidenceAnswer] = Field(default_factory=list)


class GenerateRequest(BaseModel):
    products: list[str]
    window_days: int = 30
    from_date: str | None = None
    to_date: str | None = None
    regenerate: bool = False


class GenerateResponse(BaseModel):
    run_id: str
    status: str


# ─── Read ─────────────────────────────────────────────────────────────────────

def _snippet(text: str, limit: int = 300) -> str:
    collapsed = " ".join((text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit].rstrip() + "…"


def _hydrate(rem: Remediation, session: Session) -> RemediationItem:
    """Attach the real source questions/answers so every claim is auditable."""
    try:
        q_ids = [int(x) for x in json.loads(rem.evidence_question_so_ids or "[]")]
    except (json.JSONDecodeError, ValueError, TypeError):
        q_ids = []
    try:
        a_ids = [int(x) for x in json.loads(rem.evidence_answer_so_ids or "[]")]
    except (json.JSONDecodeError, ValueError, TypeError):
        a_ids = []

    ev_q: list[EvidenceQuestion] = []
    if q_ids:
        rows = session.exec(select(Question).where(Question.so_id.in_(q_ids))).all()  # type: ignore[attr-defined]
        by_id = {q.so_id: q for q in rows}
        for sid in q_ids:
            q = by_id.get(sid)
            if q:
                ev_q.append(EvidenceQuestion(
                    so_id=q.so_id, title=q.title, url=_question_url(q.so_id),
                ))

    ev_a: list[EvidenceAnswer] = []
    if a_ids:
        rows_a = session.exec(select(Answer).where(Answer.so_id.in_(a_ids))).all()  # type: ignore[attr-defined]
        by_aid = {a.so_id: a for a in rows_a}
        for sid in a_ids:
            a = by_aid.get(sid)
            if a:
                ev_a.append(EvidenceAnswer(
                    so_id=a.so_id, question_so_id=a.question_so_id,
                    snippet=_snippet(a.body), is_accepted=a.is_accepted, score=a.score,
                ))

    return RemediationItem(
        main_category=rem.main_category,
        sub_category=rem.sub_category,
        question_count=rem.question_count,
        distinct_users=rem.distinct_users,
        root_cause=rem.root_cause,
        solution=rem.solution,
        prevention=rem.prevention,
        confidence=rem.confidence,
        grounded=rem.grounded,
        model=rem.model,
        generated_at=rem.generated_at,
        evidence_questions=ev_q,
        evidence_answers=ev_a,
    )


@router.get("", response_model=list[RemediationItem])
def list_remediations(
    product: str = Query(..., description="Product/tag the remediations were generated for"),
    window: int = Query(30, description="Window in days the remediations were generated for"),
    session: Session = Depends(get_session),
) -> list[RemediationItem]:
    """Return stored grounded remediations for a product/window, grounded first."""
    rows = session.exec(
        select(Remediation).where(
            Remediation.product_tag == product,
            Remediation.window_days == window,
        )
    ).all()
    items = [_hydrate(r, session) for r in rows]
    items.sort(key=lambda r: (not r.grounded, -r.question_count))
    return items


# ─── Generate (background + SSE) ──────────────────────────────────────────────

async def _run_remediation(
    run_id: str,
    products: list[str],
    window_days: int,
    queue: asyncio.Queue[dict[str, Any] | None],
    from_date: str | None,
    to_date: str | None,
    regenerate: bool,
) -> None:
    try:
        service = RemediationService()
        await service.run(
            products=products,
            window_days=window_days,
            engine=app_engine,
            queue=queue,
            from_date=from_date,
            to_date=to_date,
            regenerate=regenerate,
        )
    except Exception as exc:  # the service normally handles this, but be safe
        log.error("remediation_background_error", run_id=run_id, error=str(exc))
        await queue.put({"type": "error", "message": str(exc)})
        await queue.put(None)


@router.post("/generate", response_model=GenerateResponse)
async def generate_remediations(
    body: GenerateRequest,
    background_tasks: BackgroundTasks,
) -> GenerateResponse:
    """Start a background grounded-remediation run; stream progress from /stream."""
    if not body.products:
        raise HTTPException(status_code=422, detail="products list must not be empty")

    run_id = uuid.uuid4().hex
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    _run_queues[run_id] = queue

    background_tasks.add_task(
        _run_remediation, run_id, body.products, body.window_days, queue,
        body.from_date, body.to_date, body.regenerate,
    )
    log.info("remediation_started", run_id=run_id, products=body.products)
    return GenerateResponse(run_id=run_id, status="started")


@router.get("/stream")
async def stream_remediation(run_id: str) -> StreamingResponse:
    """SSE stream of remediation progress events for the given run_id."""
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
            _run_queues.pop(run_id, None)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
