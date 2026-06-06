"""
Tests for services/classifier.py.

Ollama HTTP calls are mocked via httpx.MockTransport.
Database uses in-memory SQLite — no disk I/O.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from app.models import Classification, Question
from services.classifier import (
    _NOISE_MAIN,
    _NOISE_SUB_DUPLICATE,
    _NOISE_SUB_INVALID,
    ClassifierService,
    _build_batch_prompt,
    _build_single_prompt,
    _parse_single,
)

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_engine():  # type: ignore[return]
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


def _insert_question(engine: Any, so_id: int = 1, title: str = "How to do X?") -> Question:
    """Insert a Question and return the refreshed ORM object (with DB-assigned id)."""
    q = Question(
        so_id=so_id,
        title=title,
        body="Some body text explaining the issue.",
        tags="[]",
        score=5,
        view_count=100,
        created_at=datetime(2024, 1, 1),
        author_id=42,
        answer_count=1,
        has_accepted=False,
    )
    with Session(engine) as session:
        session.add(q)
        session.commit()
        session.refresh(q)
    return q


def _ollama_resp(payload: Any, status: int = 200) -> httpx.Response:
    """Build a mock Ollama /api/generate response with a JSON-encoded payload."""
    body = json.dumps({"model": "llama3.1:8b", "response": json.dumps(payload), "done": True})
    return httpx.Response(
        status,
        content=body.encode(),
        headers={"content-type": "application/json"},
    )


def _valid_cls(
    main: str = "Technical",
    sub: str = "Reliability issues or instability",
    confidence: float = 0.9,
    reason: str = "test",
) -> dict[str, Any]:
    return {"main": main, "sub": sub, "confidence": confidence, "reason": reason}


# ─── Mock chroma / embedding helpers ─────────────────────────────────────────


class _MockChroma:
    """Returns a fixed list of duplicate so_ids."""

    def __init__(self, dup_ids: list[int] | None = None) -> None:
        self._dup_ids = dup_ids or []

    def find_duplicates(self, so_id: int, embedding: list[float], **_kw: Any) -> list[int]:
        return [d for d in self._dup_ids if d != so_id]


class _MockEmbedSvc:
    async def embed_question(self, title: str, body: str) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]


# ─── _build_*_prompt smoke tests ─────────────────────────────────────────────


def test_batch_prompt_contains_taxonomy() -> None:
    prompt = _build_batch_prompt([{"title": "T", "body": "B"}])
    assert "Technical" in prompt
    assert "Reliability issues or instability" in prompt


def test_single_prompt_contains_taxonomy() -> None:
    prompt = _build_single_prompt("My title", "My body")
    assert "Security / Compliance" in prompt
    assert "Adoption / Migration" in prompt


def test_batch_prompt_strict_adds_warning() -> None:
    prompt = _build_batch_prompt([{"title": "T", "body": "B"}], strict=True)
    assert "CRITICAL" in prompt


def test_single_prompt_strict_adds_warning() -> None:
    prompt = _build_single_prompt("T", "B", strict=True)
    assert "CRITICAL" in prompt


# ─── _parse_single ────────────────────────────────────────────────────────────


def test_parse_valid_classification() -> None:
    result = _parse_single(_valid_cls())
    assert result is not None
    main, sub, conf, reason = result
    assert main == "Technical"
    assert sub == "Reliability issues or instability"
    assert conf == 0.9


def test_parse_invalid_main_returns_none() -> None:
    assert _parse_single(_valid_cls(main="Unknown Category")) is None


def test_parse_invalid_sub_returns_none() -> None:
    assert _parse_single(_valid_cls(sub="Made-up subcategory")) is None


def test_parse_non_dict_returns_none() -> None:
    assert _parse_single("not a dict") is None
    assert _parse_single(42) is None
    assert _parse_single(None) is None


def test_parse_noise_category_is_valid() -> None:
    raw = {"main": _NOISE_MAIN, "sub": _NOISE_SUB_INVALID, "confidence": 0.99, "reason": "x"}
    result = _parse_single(raw)
    assert result is not None
    assert result[0] == _NOISE_MAIN


# ─── classify_questions: valid batch ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_valid_question_persists_to_db() -> None:
    engine = _make_engine()
    q = _insert_question(engine, so_id=1)

    def handler(req: httpx.Request) -> httpx.Response:
        r = _ollama_resp([_valid_cls()])
        r.request = req
        return r

    svc = ClassifierService(ollama_url="http://ollama.test", transport=httpx.MockTransport(handler))
    results = await svc.classify_questions([q], engine)

    assert len(results) == 1
    assert results[0].main_category == "Technical"
    assert results[0].sub_category == "Reliability issues or instability"
    assert results[0].is_noise is False

    with Session(engine) as session:
        cls = session.exec(
            select(Classification).where(Classification.question_id == q.id)
        ).one()
    assert cls.main_category == "Technical"
    assert cls.is_noise is False


@pytest.mark.asyncio
async def test_classify_sets_correct_model_name() -> None:
    engine = _make_engine()
    q = _insert_question(engine, so_id=2)

    def handler(req: httpx.Request) -> httpx.Response:
        r = _ollama_resp([_valid_cls()])
        r.request = req
        return r

    svc = ClassifierService(ollama_url="http://ollama.test", transport=httpx.MockTransport(handler))
    await svc.classify_questions([q], engine)

    with Session(engine) as session:
        cls = session.exec(
            select(Classification).where(Classification.question_id == q.id)
        ).one()
    assert cls.model == "llama3.1:8b"


# ─── classify_questions: invalid → retry → fallback ──────────────────────────


@pytest.mark.asyncio
async def test_invalid_batch_response_triggers_single_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("services.classifier._RETRY_WAIT_MIN", 0)
    monkeypatch.setattr("services.classifier._RETRY_WAIT_MAX", 0)

    engine = _make_engine()
    q = _insert_question(engine, so_id=3)
    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        payload = json.loads(req.content)
        # Batch prompt uniquely contains "JSON array"; single prompt has "single JSON object"
        if "json array" in payload["prompt"].lower():
            r = _ollama_resp([{"main": "WRONG", "sub": "WRONG", "confidence": 0.5, "reason": "x"}])
        else:
            # Single strict retry — return valid
            r = _ollama_resp(_valid_cls())
        r.request = req
        return r

    svc = ClassifierService(ollama_url="http://ollama.test", transport=httpx.MockTransport(handler))
    results = await svc.classify_questions([q], engine)

    # At minimum: 1 batch call + 1 single retry
    assert call_count >= 2
    assert len(results) == 1
    assert results[0].main_category == "Technical"
    assert results[0].is_noise is False


@pytest.mark.asyncio
async def test_invalid_batch_and_retry_forces_noise_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("services.classifier._RETRY_WAIT_MIN", 0)
    monkeypatch.setattr("services.classifier._RETRY_WAIT_MAX", 0)

    engine = _make_engine()
    q = _insert_question(engine, so_id=4)

    def handler(req: httpx.Request) -> httpx.Response:
        # Both batch and single retry return invalid output
        r = _ollama_resp({"bad": "data"})
        r.request = req
        return r

    svc = ClassifierService(ollama_url="http://ollama.test", transport=httpx.MockTransport(handler))
    results = await svc.classify_questions([q], engine)

    assert len(results) == 1
    assert results[0].main_category == _NOISE_MAIN
    assert results[0].sub_category == _NOISE_SUB_INVALID
    assert results[0].confidence == 0.0
    assert results[0].is_noise is True

    # Must still persist the fallback row
    with Session(engine) as session:
        cls = session.exec(
            select(Classification).where(Classification.question_id == q.id)
        ).one()
    assert cls.is_noise is True


# ─── classify_questions: batch never crashes on one bad item ──────────────────


@pytest.mark.asyncio
async def test_batch_survives_one_bad_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Batch of 3 questions. The LLM returns invalid output for item 2.
    Items 1 and 3 should classify correctly; item 2 falls back to noise.
    The whole batch must complete without raising.
    """
    monkeypatch.setattr("services.classifier._RETRY_WAIT_MIN", 0)
    monkeypatch.setattr("services.classifier._RETRY_WAIT_MAX", 0)

    engine = _make_engine()
    q1 = _insert_question(engine, so_id=10, title="Q1")
    q2 = _insert_question(engine, so_id=11, title="Q2 bad")
    q3 = _insert_question(engine, so_id=12, title="Q3")

    good = _valid_cls()
    batch_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal batch_count
        payload = json.loads(req.content)
        prompt: str = payload["prompt"]
        if "array" in prompt.lower() or "questions" in prompt.lower():
            # Batch call: good, invalid, good
            batch_count += 1
            r = _ollama_resp([good, {"bad": "data"}, good])
        else:
            # Single retry for item 2 — return fallback noise
            r = _ollama_resp({"also": "bad"})
        r.request = req
        return r

    svc = ClassifierService(ollama_url="http://ollama.test", transport=httpx.MockTransport(handler))
    results = await svc.classify_questions([q1, q2, q3], engine)

    assert len(results) == 3
    assert results[0].main_category == "Technical"
    assert results[0].is_noise is False
    assert results[1].is_noise is True
    assert results[1].sub_category == _NOISE_SUB_INVALID
    assert results[2].main_category == "Technical"
    assert results[2].is_noise is False

    # All 3 rows persisted
    with Session(engine) as session:
        all_cls = session.exec(select(Classification)).all()
    assert len(all_cls) == 3


# ─── classify_questions: idempotency ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_idempotent_on_rerun() -> None:
    """Second call with same questions must skip — not re-classify — existing rows."""
    engine = _make_engine()
    q = _insert_question(engine, so_id=20)
    call_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        r = _ollama_resp([_valid_cls()])
        r.request = req
        return r

    svc = ClassifierService(ollama_url="http://ollama.test", transport=httpx.MockTransport(handler))

    r1 = await svc.classify_questions([q], engine)
    r2 = await svc.classify_questions([q], engine)

    # LLM called only once (second run skips the already-classified row)
    assert call_count == 1
    assert len(r1) == 1
    assert len(r2) == 0  # nothing new to classify

    # Exactly one row in DB
    with Session(engine) as session:
        rows = session.exec(select(Classification)).all()
    assert len(rows) == 1


# ─── classify_questions: duplicate detection ──────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_question_routed_to_noise_without_llm() -> None:
    """
    A question whose so_id appears as a duplicate in ChromaStore must be classified
    as Misuse/Noise → Duplicate questions without calling the LLM.
    """
    engine = _make_engine()
    q_orig = _insert_question(engine, so_id=30, title="Original question")
    q_dup = _insert_question(engine, so_id=31, title="Same question rephrased")

    llm_called = False

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal llm_called
        llm_called = True
        r = _ollama_resp([_valid_cls()])
        r.request = req
        return r

    # ChromaStore says so_id=30 is a duplicate of q_dup (so_id=31)
    chroma = _MockChroma(dup_ids=[q_orig.so_id])
    embed_svc = _MockEmbedSvc()

    svc = ClassifierService(
        ollama_url="http://ollama.test",
        transport=httpx.MockTransport(handler),
        chroma_store=chroma,
        embedding_svc=embed_svc,
    )
    results = await svc.classify_questions([q_dup], engine)

    assert len(results) == 1
    assert results[0].main_category == _NOISE_MAIN
    assert results[0].sub_category == _NOISE_SUB_DUPLICATE
    assert results[0].is_noise is True
    assert not llm_called   # LLM never called for a duplicate


@pytest.mark.asyncio
async def test_non_duplicate_question_uses_llm() -> None:
    """A question with no duplicates in ChromaStore is classified via LLM."""
    engine = _make_engine()
    q = _insert_question(engine, so_id=40)
    llm_called = False

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal llm_called
        llm_called = True
        r = _ollama_resp([_valid_cls()])
        r.request = req
        return r

    chroma = _MockChroma(dup_ids=[])   # no duplicates
    embed_svc = _MockEmbedSvc()

    svc = ClassifierService(
        ollama_url="http://ollama.test",
        transport=httpx.MockTransport(handler),
        chroma_store=chroma,
        embedding_svc=embed_svc,
    )
    results = await svc.classify_questions([q], engine)

    assert llm_called
    assert results[0].main_category == "Technical"
    assert not results[0].is_noise


# ─── classify_questions: empty input ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_empty_list_returns_empty() -> None:
    engine = _make_engine()
    llm_called = False

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal llm_called
        llm_called = True
        return _ollama_resp([])  # should never be called

    svc = ClassifierService(ollama_url="http://ollama.test", transport=httpx.MockTransport(handler))
    results = await svc.classify_questions([], engine)

    assert results == []
    assert not llm_called


# ─── classify_questions: noise category is stored with is_noise=True ──────────


@pytest.mark.asyncio
async def test_noise_category_sets_is_noise_flag() -> None:
    engine = _make_engine()
    q = _insert_question(engine, so_id=50, title="test")

    def handler(req: httpx.Request) -> httpx.Response:
        r = _ollama_resp([{
            "main": _NOISE_MAIN,
            "sub": "Incomplete or low-quality questions",
            "confidence": 0.99,
            "reason": "junk",
        }])
        r.request = req
        return r

    svc = ClassifierService(ollama_url="http://ollama.test", transport=httpx.MockTransport(handler))
    results = await svc.classify_questions([q], engine)

    assert results[0].is_noise is True
    with Session(engine) as session:
        cls = session.exec(
            select(Classification).where(Classification.question_id == q.id)
        ).one()
    assert cls.is_noise is True


# ─── _is_retryable unit tests ──────────────────────────────────────────────────


def test_is_retryable_timeout() -> None:
    from services.classifier import _is_retryable
    assert _is_retryable(httpx.ReadTimeout("mock timeout")) is True


def test_is_retryable_connect_timeout() -> None:
    from services.classifier import _is_retryable
    assert _is_retryable(httpx.ConnectTimeout("mock timeout")) is True


def test_is_retryable_429() -> None:
    from services.classifier import _is_retryable
    req = httpx.Request("POST", "http://ollama.test/api/generate")
    resp = httpx.Response(429, content=b"", request=req)
    exc = httpx.HTTPStatusError("429", request=req, response=resp)
    assert _is_retryable(exc) is True


def test_is_retryable_500() -> None:
    from services.classifier import _is_retryable
    req = httpx.Request("POST", "http://ollama.test/api/generate")
    resp = httpx.Response(500, content=b"", request=req)
    exc = httpx.HTTPStatusError("500", request=req, response=resp)
    assert _is_retryable(exc) is True


def test_is_retryable_401_returns_false() -> None:
    from services.classifier import _is_retryable
    req = httpx.Request("POST", "http://ollama.test/api/generate")
    resp = httpx.Response(401, content=b"", request=req)
    exc = httpx.HTTPStatusError("401", request=req, response=resp)
    assert _is_retryable(exc) is False


def test_is_retryable_generic_exception_returns_false() -> None:
    from services.classifier import _is_retryable
    assert _is_retryable(ValueError("not retryable")) is False


# ─── _parse_single: confidence fallback ───────────────────────────────────────


def test_parse_confidence_non_float_defaults_to_zero() -> None:
    raw = {
        "main": "Technical",
        "sub": "Reliability issues or instability",
        "confidence": "not-a-number",
        "reason": "x",
    }
    result = _parse_single(raw)
    assert result is not None
    _main, _sub, conf, _reason = result
    assert conf == 0.0


# ─── batch LLM: dict-wrapped array response ───────────────────────────────────


@pytest.mark.asyncio
async def test_batch_dict_wrapped_array_response() -> None:
    """
    Some Ollama versions wrap the JSON array in a dict, e.g. {"results": [...]}.
    The classifier should unwrap the first list value and use it.
    """
    engine = _make_engine()
    q = _insert_question(engine, so_id=60)

    def handler(req: httpx.Request) -> httpx.Response:
        r = _ollama_resp({"results": [_valid_cls()]})
        r.request = req
        return r

    svc = ClassifierService(ollama_url="http://ollama.test", transport=httpx.MockTransport(handler))
    results = await svc.classify_questions([q], engine)

    assert len(results) == 1
    assert results[0].main_category == "Technical"
    assert not results[0].is_noise


@pytest.mark.asyncio
async def test_batch_single_question_dict_response() -> None:
    """
    When a single-question batch is sent, the model may return a plain dict
    (not a list). This should be treated as the one result.
    """
    engine = _make_engine()
    q = _insert_question(engine, so_id=61)

    def handler(req: httpx.Request) -> httpx.Response:
        r = _ollama_resp(_valid_cls())  # plain dict, not a list
        r.request = req
        return r

    svc = ClassifierService(ollama_url="http://ollama.test", transport=httpx.MockTransport(handler))
    results = await svc.classify_questions([q], engine)

    assert len(results) == 1
    assert results[0].main_category == "Technical"


# ─── batch LLM: HTTP failure cascades to noise fallback ──────────────────────


@pytest.mark.asyncio
async def test_batch_http_failure_falls_through_to_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    If the batch HTTP call raises a non-retryable exception (ConnectError),
    _batch_llm_call returns all-None → every question falls back to noise.
    The batch must not crash.
    """
    monkeypatch.setattr("services.classifier._RETRY_WAIT_MIN", 0)
    monkeypatch.setattr("services.classifier._RETRY_WAIT_MAX", 0)

    engine = _make_engine()
    q = _insert_question(engine, so_id=70)

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    svc = ClassifierService(ollama_url="http://ollama.test", transport=httpx.MockTransport(handler))
    results = await svc.classify_questions([q], engine)

    assert len(results) == 1
    assert results[0].is_noise is True
    assert results[0].main_category == _NOISE_MAIN
    assert results[0].confidence == 0.0
