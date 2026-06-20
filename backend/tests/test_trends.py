"""F2 — rising-volume detector: /api/insights/trends."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from app.dates import utcnow
from app.models import Classification, Question
from routers.insights import get_session, router


def _engine():  # type: ignore[no-untyped-def]
    e = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(e)
    return e


def _seed(engine, *, recent: int, baseline_only: int) -> None:  # type: ignore[no-untyped-def]
    """Seed `recent` questions in the last 3 days + `baseline_only` more in the
    7–30 day range, all tagged 'python' and classified Technical/Reliability."""
    now = utcnow()
    with Session(engine) as s:
        next_id = 1
        for i in range(recent):
            s.add(Question(
                so_id=next_id, title=f"r{i}", body="b", tags=json.dumps(["python"]),
                score=0, view_count=0, created_at=now - timedelta(days=2),
                author_id=10 + i, answer_count=0, has_accepted=False,
            ))
            next_id += 1
        for i in range(baseline_only):
            s.add(Question(
                so_id=next_id, title=f"b{i}", body="b", tags=json.dumps(["python"]),
                score=0, view_count=0, created_at=now - timedelta(days=20),
                author_id=200 + i, answer_count=0, has_accepted=False,
            ))
            next_id += 1
        # Plus one quiet category that should NOT be flagged.
        s.add(Question(
            so_id=next_id, title="q", body="b", tags=json.dumps(["python"]),
            score=0, view_count=0, created_at=now - timedelta(days=2),
            author_id=999, answer_count=0, has_accepted=False,
        ))
        s.commit()
        from sqlmodel import select as _select
        rows = list(s.exec(_select(Question)).all())
        rising = [q for q in rows if q.title.startswith("r") or q.title.startswith("b")]
        quiet = [q for q in rows if q.title == "q"]
        for q in rising:
            s.add(Classification(
                question_id=q.id, main_category="Technical",
                sub_category="Reliability", is_noise=False,
                confidence=0.9, model="m",
            ))
        for q in quiet:
            s.add(Classification(
                question_id=q.id, main_category="Documentation",
                sub_category="Missing", is_noise=False,
                confidence=0.9, model="m",
            ))
        s.commit()


@pytest.fixture()
def client():  # type: ignore[no-untyped-def]
    engine = _engine()

    def override() -> Any:
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override
    return engine, TestClient(app)


def test_flags_category_with_2x_recent_spike(client) -> None:  # type: ignore[no-untyped-def]
    engine, c = client
    # 5 recent + 2 trailing (over 23 day trailing window) → trailing_avg ≈ 0.6 for
    # a 7d-equivalent window. Recent=5 vs ~0.6 ⇒ multiplier ~8x, clearly rising.
    _seed(engine, recent=5, baseline_only=2)
    r = c.get("/api/insights/trends?product=python&recent_days=7&baseline_days=30&threshold=2")
    assert r.status_code == 200
    items = r.json()
    rising = [t for t in items if t["is_rising"]]
    assert any(t["sub_category"] == "Reliability" for t in rising)
    tech = next(t for t in items if t["sub_category"] == "Reliability")
    assert tech["recent_count"] == 5
    assert tech["multiplier"] >= 2.0


def test_quiet_category_is_not_flagged(client) -> None:  # type: ignore[no-untyped-def]
    engine, c = client
    _seed(engine, recent=5, baseline_only=2)
    items = c.get("/api/insights/trends?product=python&recent_days=7&baseline_days=30").json()
    quiet = next(t for t in items if t["sub_category"] == "Missing")
    assert quiet["is_rising"] is False
    assert quiet["recent_count"] == 1


def test_min_recent_floor_prevents_noise_flag(client) -> None:  # type: ignore[no-untyped-def]
    """1 recent question with 0 baseline is technically infinite multiplier
    but shouldn't be flagged — the min_recent floor blocks it."""
    engine, c = client
    _seed(engine, recent=1, baseline_only=0)
    items = c.get(
        "/api/insights/trends?product=python&recent_days=7&baseline_days=30&min_recent=2"
    ).json()
    tech = next(t for t in items if t["sub_category"] == "Reliability")
    assert tech["recent_count"] == 1
    assert tech["is_rising"] is False


def test_recent_must_be_less_than_baseline(client) -> None:  # type: ignore[no-untyped-def]
    _, c = client
    r = c.get("/api/insights/trends?product=python&recent_days=30&baseline_days=30")
    assert r.status_code == 422


def test_empty_corpus_returns_empty_list(client) -> None:  # type: ignore[no-untyped-def]
    _, c = client
    assert c.get("/api/insights/trends?product=python").json() == []


def test_rising_items_sort_first(client) -> None:  # type: ignore[no-untyped-def]
    engine, c = client
    _seed(engine, recent=5, baseline_only=2)
    items = c.get("/api/insights/trends?product=python").json()
    assert items, "expected at least one trend item"
    # First item should be the rising one if any are flagged.
    if any(t["is_rising"] for t in items):
        assert items[0]["is_rising"] is True
