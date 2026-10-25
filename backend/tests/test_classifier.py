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
    _SYSTEM_PROMPT,
    ClassifierService,
    _align_batch,
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


def test_system_prompt_contains_taxonomy() -> None:
    # The taxonomy lives in the static system prompt (KV-cached by Ollama), not
    # in the per-call prompt.
    assert "Technical > Reliability issues or instability" in _SYSTEM_PROMPT
    assert "Security / Compliance" in _SYSTEM_PROMPT
    assert "Adoption / Migration" in _SYSTEM_PROMPT


def test_batch_prompt_contains_questions_only() -> None:
    prompt = _build_batch_prompt([{"title": "My title", "body": "My body"}])
    assert "My title" in prompt and "My body" in prompt
    assert "Reliability issues or instability" not in prompt


def test_single_prompt_is_a_batch_of_one() -> None:
    assert _build_single_prompt("T", "B") == _build_batch_prompt([{"title": "T", "body": "B"}])


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


def test_parse_invalid_main_recovered_from_unambiguous_sub() -> None:
    # The sub-category alone identifies exactly one taxonomy pair.
    result = _parse_single(_valid_cls(main="Unknown Category"))
    assert result is not None and result[:2] == ("Technical", "Reliability issues or instability")


def test_parse_contradicting_main_and_sub_returns_none() -> None:
    # A real main paired with another main's sub is a contradiction, not a typo.
    assert _parse_single(_valid_cls(main="Product")) is None


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
        # Only the strict per-question retry carries "CRITICAL".
        if "CRITICAL" not in payload["prompt"]:
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
        if "CRITICAL" not in prompt:
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


def test_is_retryable_connect_timeout_is_not_retried() -> None:
    # Nothing is listening: back-off only delays the "Ollama unreachable" report.
    from services.classifier import _is_retryable
    assert _is_retryable(httpx.ConnectTimeout("mock timeout")) is False


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
async def test_unreachable_ollama_records_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Regression: when Ollama could not be reached, every question used to be
    saved as Misuse / Noise — and since classification is idempotent, it then
    stayed noise forever. Now nothing is recorded, the run stops early, and the
    reason is reported so the questions are picked up on the next run.
    """
    monkeypatch.setattr("services.classifier._RETRY_WAIT_MIN", 0)
    monkeypatch.setattr("services.classifier._RETRY_WAIT_MAX", 0)
    monkeypatch.setattr("services.classifier.settings.classify_batch_size", 1)

    engine = _make_engine()
    qs = [_insert_question(engine, so_id=70), _insert_question(engine, so_id=71)]
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("connection refused")

    svc = ClassifierService(ollama_url="http://ollama.test", transport=httpx.MockTransport(handler))
    results = await svc.classify_questions(qs, engine)

    assert results == []
    assert calls == 1                       # stopped after the first failure
    assert svc.skipped == 2
    assert svc.unavailable_error and "Cannot reach Ollama" in svc.unavailable_error
    with Session(engine) as session:
        assert session.exec(select(Classification)).all() == []


@pytest.mark.asyncio
async def test_missing_model_is_reported_not_filed_as_noise() -> None:
    engine = _make_engine()
    q = _insert_question(engine, so_id=72)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'nope:1b' not found"}, request=req)

    svc = ClassifierService(ollama_url="http://ollama.test", transport=httpx.MockTransport(handler))
    results = await svc.classify_questions([q], engine)

    assert results == []
    assert svc.unavailable_error and "ollama pull" in svc.unavailable_error
    with Session(engine) as session:
        assert session.exec(select(Classification)).all() == []


@pytest.mark.asyncio
async def test_stale_fallback_rows_are_reclassified() -> None:
    """Rows left by the old failure path (noise, confidence 0.0) are retried and
    replaced; genuine verdicts are left alone."""
    engine = _make_engine()
    stale = _insert_question(engine, so_id=80)
    kept = _insert_question(engine, so_id=81)
    with Session(engine) as session:
        session.add(Classification(
            question_id=stale.id, main_category=_NOISE_MAIN,
            sub_category=_NOISE_SUB_INVALID, confidence=0.0, is_noise=True, model="m",
        ))
        session.add(Classification(
            question_id=kept.id, main_category="Product",
            sub_category="Feature Gap", confidence=0.9, is_noise=False, model="m",
        ))
        session.commit()

    def handler(req: httpx.Request) -> httpx.Response:
        return _ollama_resp({"q1": {"label": "Technical > Performance or scaling issues",
                                    "confidence": 0.8}})

    svc = ClassifierService(ollama_url="http://ollama.test", transport=httpx.MockTransport(handler))
    results = await svc.classify_questions([stale, kept], engine)

    assert [r.question_id for r in results] == [stale.id]
    with Session(engine) as session:
        rows = {c.question_id: c for c in session.exec(select(Classification)).all()}
    assert len(rows) == 2
    assert rows[stale.id].sub_category == "Performance or scaling issues"
    assert rows[kept.id].sub_category == "Feature Gap"


# ─── Constrained output / small-model optimisations ──────────────────────────


@pytest.mark.asyncio
async def test_request_is_constrained_cached_and_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("services.classifier.settings.classify_batch_size", 3)
    engine = _make_engine()
    qs = [_insert_question(engine, so_id=100 + i, title=f"Question number {i}") for i in range(3)]
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return _ollama_resp({
            f"q{i + 1}": {"label": "Technical > Performance or scaling issues", "confidence": 0.9}
            for i in range(3)
        })

    svc = ClassifierService(ollama_url="http://ollama.test", transport=httpx.MockTransport(handler))
    results = await svc.classify_questions(qs, engine)

    assert len(seen) == 1 and len(results) == 3
    body = seen[0]
    # Static instructions go in `system` — identical on every call, so Ollama
    # can reuse the KV cache for them.
    assert body["system"] == _SYSTEM_PROMPT
    # Grammar-constrained: exactly q1..q3, each one of the taxonomy labels.
    fmt = body["format"]
    assert fmt["required"] == ["q1", "q2", "q3"]
    labels = fmt["properties"]["q1"]["properties"]["label"]["enum"]
    assert "Technical > Performance or scaling issues" in labels and len(labels) == 29
    opts = body["options"]
    assert opts["temperature"] == 0 and opts["num_ctx"] >= 4096
    assert 0 < opts["num_predict"] <= 3 * 40 + 64
    assert body["think"] is False


@pytest.mark.asyncio
async def test_legacy_ollama_without_schema_support_falls_back_to_json_mode() -> None:
    from services import ollama as ollama_mod

    engine = _make_engine()
    q = _insert_question(engine, so_id=110)
    formats: list[Any] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        formats.append(body["format"])
        if isinstance(body["format"], dict):
            return httpx.Response(400, json={"error": "invalid format"}, request=req)
        assert "think" not in body
        return _ollama_resp({"q1": {"label": "Product > Feature Gap", "confidence": 0.9}})

    url = "http://legacy-ollama.test"
    try:
        svc = ClassifierService(ollama_url=url, transport=httpx.MockTransport(handler))
        results = await svc.classify_questions([q], engine)
    finally:
        ollama_mod._LEGACY_SERVERS.discard(url)

    assert results[0].sub_category == "Feature Gap"
    assert isinstance(formats[0], dict) and formats[1] == "json"


@pytest.mark.asyncio
async def test_contentless_question_skips_the_model() -> None:
    engine = _make_engine()
    q = _insert_question(engine, so_id=120, title="test")
    with Session(engine) as session:
        row = session.get(Question, q.id)
        assert row is not None
        row.body = "<p>test</p>"
        session.add(row)
        session.commit()
        session.refresh(row)
        q = row

    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("model must not be called")

    svc = ClassifierService(ollama_url="http://ollama.test", transport=httpx.MockTransport(handler))
    results = await svc.classify_questions([q], engine)
    assert results[0].is_noise is True
    assert results[0].confidence > 0      # a verdict, not a stale fallback


@pytest.mark.asyncio
async def test_progress_is_reported_per_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("services.classifier.settings.classify_batch_size", 2)
    engine = _make_engine()
    qs = [_insert_question(engine, so_id=130 + i) for i in range(5)]

    def handler(req: httpx.Request) -> httpx.Response:
        n = len(json.loads(req.content)["format"]["required"])
        return _ollama_resp({
            f"q{i + 1}": {"label": "Product > Feature Gap", "confidence": 0.9} for i in range(n)
        })

    ticks: list[tuple[int, int]] = []

    async def progress(done: int, total: int) -> None:
        ticks.append((done, total))

    svc = ClassifierService(ollama_url="http://ollama.test", transport=httpx.MockTransport(handler))
    await svc.classify_questions(qs, engine, progress=progress)
    assert ticks == [(2, 5), (4, 5), (5, 5)]


@pytest.mark.asyncio
async def test_concurrent_batches_classify_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("services.classifier.settings.classify_batch_size", 2)
    monkeypatch.setattr("services.classifier.settings.classify_concurrency", 3)
    engine = _make_engine()
    qs = [_insert_question(engine, so_id=140 + i) for i in range(7)]

    def handler(req: httpx.Request) -> httpx.Response:
        n = len(json.loads(req.content)["format"]["required"])
        return _ollama_resp({
            f"q{i + 1}": {"label": "Product > Feature Gap", "confidence": 0.9} for i in range(n)
        })

    svc = ClassifierService(ollama_url="http://ollama.test", transport=httpx.MockTransport(handler))
    results = await svc.classify_questions(qs, engine)
    assert sorted(r.question_id for r in results) == sorted(q.id for q in qs)


def test_oversized_batch_is_split_to_fit_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("services.classifier.settings.ollama_num_ctx", 2048)
    svc = ClassifierService(ollama_url="http://ollama.test")
    long_body = "word " * 400
    prompt = _build_batch_prompt([{"title": "t", "body": long_body}] * 10)
    assert svc._fits_context(prompt, 10) is False


def test_body_html_is_stripped_before_truncation() -> None:
    from services.classifier import _clean_text
    raw = "<p>Error&nbsp;from <code>gcloud sql connect</code>:</p>\n<pre>timeout</pre>"
    assert _clean_text(raw) == "Error from gcloud sql connect : timeout"
    assert len(_clean_text("x " * 500, 50)) <= 52


def test_every_taxonomy_pair_has_a_definition() -> None:
    from app.taxonomy import TAXONOMY
    from services.classifier import _DEFINITIONS
    pairs = {(m, s) for m, subs in TAXONOMY.items() for s in subs}
    assert pairs == set(_DEFINITIONS)


def test_parse_accepts_label_shape_and_sloppy_text() -> None:
    assert _parse_single({"label": "Product > Feature Gap", "confidence": 0.8})[:3] == (
        "Product", "Feature Gap", 0.8)
    # Case/spacing slips in plain JSON mode are recovered exactly, never guessed.
    assert _parse_single({"label": "product>feature gap", "confidence": 90})[:3] == (
        "Product", "Feature Gap", 0.9)
    assert _parse_single({"label": "Product > Something else"}) is None


# ─── Batch alignment (regressions) ────────────────────────────────────────────


def test_align_batch_maps_by_index_when_present() -> None:
    """Out-of-order items are placed by their index, not their position."""
    items = [
        {"index": 3, "main": "C"},
        {"index": 1, "main": "A"},
        {"index": 2, "main": "B"},
    ]
    assert [r["main"] for r in _align_batch(items, 3)] == ["A", "B", "C"]


def test_align_batch_dropped_item_does_not_shift_neighbours() -> None:
    """The regression this guards: a model skipping question 2 used to shift
    every later result onto the wrong question. The gap must stay a gap."""
    items = [{"index": 1, "main": "A"}, {"index": 3, "main": "C"}]
    out = _align_batch(items, 3)
    assert out[0] is not None and out[0]["main"] == "A"
    assert out[1] is None            # retried individually, not filled with C
    assert out[2] is not None and out[2]["main"] == "C"


def test_align_batch_positional_when_counts_match_and_no_index() -> None:
    """No indices but the right number of items — position is sound here."""
    items = [{"main": "A"}, {"main": "B"}]
    assert [r["main"] for r in _align_batch(items, 2)] == ["A", "B"]


def test_align_batch_miscount_without_index_yields_no_guesses() -> None:
    """Too few items and no indices: every slot goes to per-question retry
    rather than being padded into a guess."""
    assert _align_batch([{"main": "A"}], 3) == [None, None, None]


def test_align_batch_drops_unusable_indices_but_keeps_valid_ones() -> None:
    """An out-of-range or duplicate index makes that one item untrustworthy, not
    the whole batch — the item is dropped and its slot goes to retry."""
    out = _align_batch([{"index": 9, "main": "A"}, {"index": 1, "main": "B"}], 2)
    assert out[0] is not None and out[0]["main"] == "B"   # index 1 honoured
    assert out[1] is None                                  # index 9 discarded

    dup = _align_batch([{"index": 1, "main": "A"}, {"index": 1, "main": "B"}], 2)
    assert dup[0] is not None and dup[0]["main"] == "A"    # first occurrence wins
    assert dup[1] is None


def test_align_batch_prefers_index_over_position_when_they_disagree() -> None:
    """If the model states an index, that is its own claim about which question
    the result belongs to, and it beats positional order."""
    out = _align_batch([{"index": 2, "main": "SECOND"}, {"index": 1, "main": "FIRST"}], 2)
    assert [r["main"] for r in out] == ["FIRST", "SECOND"]


def test_align_batch_extra_items_are_dropped_not_misassigned() -> None:
    items = [{"index": 1, "main": "A"}, {"index": 2, "main": "B"}, {"index": 3, "main": "C"}]
    assert [r["main"] for r in _align_batch(items, 2)] == ["A", "B"]


def test_batch_prompt_requests_one_key_per_question() -> None:
    prompt = _build_batch_prompt([{"title": "T", "body": "B"}, {"title": "U", "body": "C"}])
    assert '"q1", "q2"' in prompt


# ─── Configurable batch size ──────────────────────────────────────────────────


def _seed(engine: Any, n: int) -> list[Question]:
    return [_insert_question(engine, so_id=900 + i) for i in range(n)]


def _indexed_ok(n: int) -> list[dict[str, Any]]:
    return [
        {"index": i + 1, "main": "Technical",
         "sub": "Performance or scaling issues", "confidence": 0.9, "reason": "r"}
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_classify_respects_configured_batch_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """classify_questions must chunk by settings.classify_batch_size, read at
    call time so the value can change without a restart."""
    monkeypatch.setattr("services.classifier.settings.classify_batch_size", 2)

    engine = _make_engine()
    questions = _seed(engine, 5)
    sizes: list[int] = []

    async def fake_batch(self: Any, qs: list[Any]) -> list[dict[str, Any] | None]:
        sizes.append(len(qs))
        return _indexed_ok(len(qs))

    monkeypatch.setattr(ClassifierService, "_batch_llm_call", fake_batch)
    await ClassifierService().classify_questions(questions, engine)

    assert sizes == [2, 2, 1]


@pytest.mark.asyncio
async def test_classify_batch_size_zero_falls_back_to_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A misconfigured 0 must not produce an empty range() and silently classify
    nothing."""
    monkeypatch.setattr("services.classifier.settings.classify_batch_size", 0)

    engine = _make_engine()
    questions = _seed(engine, 2)

    async def fake_batch(self: Any, qs: list[Any]) -> list[dict[str, Any] | None]:
        return _indexed_ok(len(qs))

    monkeypatch.setattr(ClassifierService, "_batch_llm_call", fake_batch)
    results = await ClassifierService().classify_questions(questions, engine)
    assert len(results) == 2
