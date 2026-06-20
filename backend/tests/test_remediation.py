"""
Tests for the grounded remediation service.

Ollama HTTP calls are mocked via httpx.MockTransport. The focus is the grounding
guarantee: cited evidence is intersected with the cluster's real source IDs, and
no model prose is stored when nothing can be anchored to real sources.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import httpx
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Answer, Classification, Question, Remediation
from services.remediation import RemediationService

pytestmark = pytest.mark.asyncio


def _make_engine():  # type: ignore[no-untyped-def]
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


def _ollama_resp(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    body = json.dumps(
        {"model": "llama3.1:8b", "response": json.dumps(payload), "done": True}
    )
    return httpx.Response(
        status, content=body.encode(), headers={"content-type": "application/json"}
    )


def _seed_cluster(
    engine, *, n_questions: int = 3, n_users: int = 2, with_answers: bool = True
) -> None:
    """Seed a single (Technical / Reliability) cluster of similar questions."""
    with Session(engine) as session:
        for i in range(n_questions):
            q = Question(
                so_id=1000 + i,
                title=f"Connection times out intermittently #{i}",
                body="The client drops after ~30s under load.",
                tags=json.dumps(["python"]),
                score=5 - i,
                view_count=100 + i,
                created_at=datetime.utcnow(),
                author_id=10 + (i % n_users),
                answer_count=1 if with_answers else 0,
                has_accepted=with_answers,
            )
            session.add(q)
            session.commit()
            session.refresh(q)
            session.add(Classification(
                question_id=q.id,
                main_category="Technical",
                sub_category="Reliability issues or instability",
                is_noise=False,
                confidence=0.9,
                model="test-model",
            ))
            if with_answers:
                session.add(Answer(
                    so_id=5000 + i,
                    question_so_id=q.so_id,
                    body="Raise the idle timeout and enable TCP keepalive on the pool.",
                    score=7,
                    is_accepted=True,
                    created_at=datetime.utcnow(),
                ))
            session.commit()


def _service(handler, calls: list | None = None) -> RemediationService:
    def wrapped(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(req)
        return handler(req)
    return RemediationService(
        ollama_url="http://ollama.test",
        transport=httpx.MockTransport(wrapped),
    )


async def _run(
    service: RemediationService, engine, regenerate: bool = False
) -> list[dict[str, Any]]:
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    await service.run(["python"], 30, engine, queue, regenerate=regenerate)
    events: list[dict[str, Any]] = []
    while True:
        ev = await queue.get()
        if ev is None:
            break
        events.append(ev)
    return events


# ── Happy path: grounded remediation persisted ────────────────────────────────

async def test_generates_grounded_remediation() -> None:
    engine = _make_engine()
    _seed_cluster(engine)

    def handler(_req: httpx.Request) -> httpx.Response:
        return _ollama_resp({
            "root_cause": "Idle connections are reaped by the load balancer.",
            "solution": "Increase the idle timeout and enable keepalive.",
            "prevention": "Document the recommended pool settings.",
            "evidence_question_ids": [1000, 1001],
            "evidence_answer_ids": [5000],
            "confidence": 0.8,
        })

    events = await _run(_service(handler), engine)

    assert any(e.get("type") == "done" for e in events)
    with Session(engine) as session:
        rems = session.exec(select(Remediation)).all()
    assert len(rems) == 1
    r = rems[0]
    assert r.grounded is True
    assert r.main_category == "Technical"
    assert "keepalive" in r.solution
    assert json.loads(r.evidence_question_so_ids) == [1000, 1001]
    assert json.loads(r.evidence_answer_so_ids) == [5000]
    assert r.confidence == pytest.approx(0.8)


# ── Grounding: invented IDs are discarded ─────────────────────────────────────

async def test_discards_invented_evidence_ids() -> None:
    engine = _make_engine()
    _seed_cluster(engine)

    def handler(_req: httpx.Request) -> httpx.Response:
        return _ollama_resp({
            "root_cause": "x",
            "solution": "y",
            "prevention": "z",
            "evidence_question_ids": [1000, 999999],   # 999999 not in cluster
            "evidence_answer_ids": [5000, 888888],     # 888888 not in cluster
            "confidence": 0.5,
        })

    await _run(_service(handler), engine)

    with Session(engine) as session:
        r = session.exec(select(Remediation)).one()
    assert r.grounded is True
    assert json.loads(r.evidence_question_so_ids) == [1000]
    assert json.loads(r.evidence_answer_so_ids) == [5000]


# ── Grounding: no valid evidence → ungrounded, no invented prose ──────────────

async def test_ungrounded_when_no_valid_evidence() -> None:
    engine = _make_engine()
    _seed_cluster(engine)

    def handler(_req: httpx.Request) -> httpx.Response:
        return _ollama_resp({
            "root_cause": "totally made up",
            "solution": "do something not in the sources",
            "prevention": "invented prevention",
            "evidence_question_ids": [424242],   # none real
            "evidence_answer_ids": [],
            "confidence": 0.99,
        })

    await _run(_service(handler), engine)

    with Session(engine) as session:
        r = session.exec(select(Remediation)).one()
    assert r.grounded is False
    assert r.root_cause == ""
    assert r.solution == ""              # invented prose is NOT stored
    assert "grounded" in r.prevention.lower()
    assert r.confidence == 0.0


# ── Threshold: too few questions/users → no clusters, no LLM call ─────────────

async def test_skips_below_threshold() -> None:
    engine = _make_engine()
    _seed_cluster(engine, n_questions=2, n_users=1)  # below 3 Qs / 2 users

    calls: list = []

    def handler(_req: httpx.Request) -> httpx.Response:
        return _ollama_resp({"evidence_question_ids": [1000]})

    events = await _run(_service(handler, calls), engine)

    assert calls == []  # model never called
    done = next(e for e in events if e.get("type") == "done")
    assert done["generated"] == 0
    with Session(engine) as session:
        assert session.exec(select(Remediation)).all() == []


# ── Caching: unchanged source set is reused on rerun ──────────────────────────

async def test_reuses_cached_when_sources_unchanged() -> None:
    engine = _make_engine()
    _seed_cluster(engine)
    calls: list = []

    def handler(_req: httpx.Request) -> httpx.Response:
        return _ollama_resp({
            "root_cause": "rc", "solution": "sol", "prevention": "prev",
            "evidence_question_ids": [1000], "evidence_answer_ids": [5000],
            "confidence": 0.7,
        })

    svc = _service(handler, calls)
    await _run(svc, engine)
    assert len(calls) == 1

    # Second run without regenerate: same hash → cached, no new LLM call.
    events = await _run(svc, engine)
    assert len(calls) == 1
    assert any(e.get("type") == "cluster_done" and e.get("cached") for e in events)

    # Forcing regenerate calls the model again.
    await _run(svc, engine, regenerate=True)
    assert len(calls) == 2


# ── Works when the cluster has no captured answers ────────────────────────────

async def test_grounded_on_questions_without_answers() -> None:
    engine = _make_engine()
    _seed_cluster(engine, with_answers=False)

    def handler(_req: httpx.Request) -> httpx.Response:
        return _ollama_resp({
            "root_cause": "shared cause",
            "solution": "No captured answer contains a verified fix.",
            "prevention": "Add a troubleshooting doc for timeouts.",
            "evidence_question_ids": [1000, 1001, 1002],
            "evidence_answer_ids": [5000],   # no answers exist → must be dropped
            "confidence": 0.4,
        })

    await _run(_service(handler), engine)

    with Session(engine) as session:
        r = session.exec(select(Remediation)).one()
    assert r.grounded is True
    assert json.loads(r.evidence_answer_so_ids) == []   # dropped, none real
    assert len(json.loads(r.evidence_question_so_ids)) == 3
