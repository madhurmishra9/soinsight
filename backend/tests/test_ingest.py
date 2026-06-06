"""
Tests for services/ingestion.py.

SO client calls are replaced by MockSOClient (async-generator duck-type).
Database uses an in-memory SQLite engine — no disk I/O.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from app.models import Question
from services.ingestion import (
    BudgetExhaustedError,
    BudgetTracker,
    IngestService,
    _map_question,
)

# ─── Shared test fixture ──────────────────────────────────────────────────────

RAW_QUESTION: dict[str, Any] = {
    "question_id": 42,
    "title": "How to configure X?",
    "body": "<p>I need help with X.</p>",
    "tags": ["python", "api"],
    "score": 7,
    "view_count": 250,
    "creation_date": 1609459200,  # 2021-01-01 00:00:00 UTC
    "owner": {"user_id": 99, "user_type": "registered"},
    "answer_count": 3,
    "is_answered": True,
}


class MockSOClient:
    """Minimal SO client duck-type that yields a fixed list of question dicts."""

    def __init__(self, questions: list[dict[str, Any]]) -> None:
        self._questions = questions

    async def iter_questions(
        self, tag: str, since: datetime, team: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        for q in self._questions:
            yield q


def _make_engine():  # type: ignore[return]
    """In-memory SQLite engine with all tables created."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


# ─── _map_question: explicit field mapping ────────────────────────────────────

def test_map_question_core_fields() -> None:
    result = _map_question(RAW_QUESTION, team_slug="team-a")
    assert result["so_id"] == 42
    assert result["title"] == "How to configure X?"
    assert result["body"] == "<p>I need help with X.</p>"
    assert result["score"] == 7
    assert result["view_count"] == 250
    assert result["answer_count"] == 3
    assert result["has_accepted"] is True
    assert result["team_slug"] == "team-a"


def test_map_question_tags_are_json_array() -> None:
    result = _map_question(RAW_QUESTION)
    tags = json.loads(result["tags"])
    assert isinstance(tags, list)
    assert "python" in tags
    assert "api" in tags


def test_map_question_author_fields() -> None:
    result = _map_question(RAW_QUESTION)
    assert result["author_id"] == 99
    assert result["author_role"] == "registered"


def test_map_question_created_at_from_unix_timestamp() -> None:
    result = _map_question(RAW_QUESTION)
    assert result["created_at"] == datetime(2021, 1, 1, 0, 0, 0)


def test_map_question_defaults_for_missing_optional_fields() -> None:
    minimal: dict[str, Any] = {"question_id": 1, "creation_date": 1609459200}
    result = _map_question(minimal)
    assert result["so_id"] == 1
    assert result["title"] == ""
    assert result["body"] == ""
    assert result["score"] == 0
    assert result["author_id"] == 0
    assert result["author_role"] is None
    assert result["team_slug"] is None


def test_map_question_missing_id_raises_key_error() -> None:
    with pytest.raises(KeyError):
        _map_question({"title": "no id here"})


def test_map_question_no_team_slug_defaults_to_none() -> None:
    result = _map_question(RAW_QUESTION)
    assert result["team_slug"] is None


# ─── BudgetTracker ────────────────────────────────────────────────────────────

def test_budget_increments_on_charge() -> None:
    b = BudgetTracker(daily_limit=10)
    b.charge(3)
    assert b.used == 3
    assert b.remaining == 7


def test_budget_exhausted_raises() -> None:
    b = BudgetTracker(daily_limit=2)
    b.charge(2)
    with pytest.raises(BudgetExhaustedError):
        b.charge(1)


def test_budget_at_limit_does_not_raise() -> None:
    b = BudgetTracker(daily_limit=5)
    b.charge(5)  # exactly at the limit — must not raise


def test_budget_multiple_charges_accumulate() -> None:
    b = BudgetTracker(daily_limit=10)
    b.charge(3)
    b.charge(4)
    assert b.used == 7


# ─── IngestService: end-to-end with mock client ───────────────────────────────

@pytest.mark.asyncio
async def test_ingest_inserts_questions() -> None:
    engine = _make_engine()
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    service = IngestService(
        client=MockSOClient([RAW_QUESTION]),  # type: ignore[arg-type]
        budget=BudgetTracker(daily_limit=100),
    )

    result = await service.run(["python"], 30, None, queue, engine)

    assert result.inserted == 1
    assert result.skipped == 0
    assert result.errors == 0
    with Session(engine) as session:
        rows = session.exec(select(Question)).all()
    assert len(rows) == 1
    assert rows[0].so_id == 42


@pytest.mark.asyncio
async def test_ingest_idempotent_on_rerun() -> None:
    """Second run with same data must skip — not insert — existing rows."""
    engine = _make_engine()
    budget = BudgetTracker(daily_limit=100)
    client = MockSOClient([RAW_QUESTION])  # type: ignore[arg-type]

    q1: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    r1 = await IngestService(client=client, budget=budget).run(
        ["python"], 30, None, q1, engine
    )

    q2: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    r2 = await IngestService(client=client, budget=budget).run(
        ["python"], 30, None, q2, engine
    )

    with Session(engine) as session:
        count = len(session.exec(select(Question)).all())

    assert count == 1      # not 2
    assert r1.inserted == 1
    assert r2.inserted == 0
    assert r2.skipped == 1


@pytest.mark.asyncio
async def test_ingest_maps_fields_to_db_row() -> None:
    engine = _make_engine()
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    service = IngestService(
        client=MockSOClient([RAW_QUESTION]),  # type: ignore[arg-type]
        budget=BudgetTracker(daily_limit=100),
    )

    await service.run(["python"], 30, "team-a", queue, engine)

    with Session(engine) as session:
        q = session.exec(select(Question).where(Question.so_id == 42)).one()

    assert q.title == "How to configure X?"
    assert q.score == 7
    assert q.author_id == 99
    assert q.has_accepted is True
    assert q.team_slug == "team-a"
    assert json.loads(q.tags) == ["python", "api"]


@pytest.mark.asyncio
async def test_ingest_multiple_questions() -> None:
    raw2: dict[str, Any] = {**RAW_QUESTION, "question_id": 43, "title": "Another Q"}
    engine = _make_engine()
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    service = IngestService(
        client=MockSOClient([RAW_QUESTION, raw2]),  # type: ignore[arg-type]
        budget=BudgetTracker(daily_limit=100),
    )

    result = await service.run(["python"], 30, None, queue, engine)

    assert result.inserted == 2
    with Session(engine) as session:
        assert len(session.exec(select(Question)).all()) == 2


@pytest.mark.asyncio
async def test_ingest_budget_stops_on_exhaustion() -> None:
    """When budget is pre-exhausted, the first tag is skipped and no rows are inserted."""
    engine = _make_engine()
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    budget = BudgetTracker(daily_limit=1)
    budget.charge(1)  # pre-exhaust

    result = await IngestService(
        client=MockSOClient([RAW_QUESTION]),  # type: ignore[arg-type]
        budget=budget,
    ).run(["python", "java"], 30, None, queue, engine)

    assert result.inserted == 0
    with Session(engine) as session:
        assert len(session.exec(select(Question)).all()) == 0


@pytest.mark.asyncio
async def test_ingest_queue_receives_done_sentinel() -> None:
    """Queue must always receive None sentinel so SSE consumers can terminate."""
    engine = _make_engine()
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    service = IngestService(
        client=MockSOClient([RAW_QUESTION]),  # type: ignore[arg-type]
        budget=BudgetTracker(daily_limit=100),
    )

    await service.run(["python"], 30, None, queue, engine)

    events = []
    while not queue.empty():
        events.append(await queue.get())

    # Last item must be None (sentinel)
    assert events[-1] is None
    # Second-to-last must be the "done" event
    assert any(e is not None and e.get("type") == "done" for e in events)
