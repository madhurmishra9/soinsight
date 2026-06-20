"""
Tests for GET /api/questions/coverage — per-tag local-data coverage read live
from the database (question/answer counts, the 'data fetched till' watermark,
and the last fetch-run time).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db import get_session
from app.models import Answer, Question, Run
from routers.questions import router as questions_router


def _make_engine():  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _client(engine) -> TestClient:  # type: ignore[no-untyped-def]
    app = FastAPI()
    app.include_router(questions_router)

    def override() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override
    return TestClient(app)


def _seed(engine) -> None:  # type: ignore[no-untyped-def]
    now = datetime.utcnow()
    with Session(engine) as s:
        s.add(Question(
            so_id=1, title="oldest", body="", tags=json.dumps(["python"]),
            score=1, view_count=1, created_at=now - timedelta(days=5), author_id=1,
            answer_count=1, has_accepted=True,
        ))
        s.add(Question(
            so_id=2, title="newest", body="", tags=json.dumps(["python", "api"]),
            score=2, view_count=2, created_at=now - timedelta(days=1), author_id=2,
            answer_count=0, has_accepted=False,
        ))
        s.add(Answer(
            so_id=900, question_so_id=1, body="ans", score=3, is_accepted=True,
            created_at=now,
        ))
        s.add(Run(
            products=json.dumps(["python"]), window_days=30, status="done",
            started_at=now - timedelta(hours=2), finished_at=now - timedelta(hours=1),
        ))
        # a still-running run must not count as a completed fetch time
        s.add(Run(
            products=json.dumps(["python"]), window_days=30, status="running",
            started_at=now,
        ))
        s.commit()


def test_coverage_reports_counts_and_watermark() -> None:
    engine = _make_engine()
    _seed(engine)
    client = _client(engine)

    rows = client.get("/api/questions/coverage", params={"products": "python"}).json()
    assert len(rows) == 1
    cov = rows[0]
    assert cov["tag"] == "python"
    assert cov["question_count"] == 2
    assert cov["answer_count"] == 1
    # Watermark = newest question's creation date (so_id 2).
    assert cov["latest_question_at"] is not None
    assert cov["earliest_question_at"] < cov["latest_question_at"]
    # last_fetch_at comes from the finished run, not the running one.
    assert cov["last_fetch_at"] is not None


def test_coverage_unknown_tag_is_zeroed() -> None:
    engine = _make_engine()
    _seed(engine)
    client = _client(engine)

    rows = client.get("/api/questions/coverage", params={"products": "does-not-exist"}).json()
    assert rows == [{
        "tag": "does-not-exist",
        "question_count": 0,
        "answer_count": 0,
        "earliest_question_at": None,
        "latest_question_at": None,
        "last_fetch_at": None,
    }]


def test_coverage_multiple_tags_and_exact_match() -> None:
    engine = _make_engine()
    _seed(engine)
    client = _client(engine)

    rows = client.get("/api/questions/coverage", params={"products": "python,api"}).json()
    by_tag = {r["tag"]: r for r in rows}
    assert by_tag["python"]["question_count"] == 2
    # "api" tag is only on so_id 2 — exact-match, not substring of "api-gateway" etc.
    assert by_tag["api"]["question_count"] == 1


def test_coverage_empty_products_returns_empty() -> None:
    engine = _make_engine()
    _seed(engine)
    client = _client(engine)
    assert client.get("/api/questions/coverage", params={"products": ""}).json() == []
