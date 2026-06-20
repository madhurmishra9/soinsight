"""Tests for the pattern snooze/dismiss API."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.dates import utcnow
from app.db import get_session
from app.models import PatternDismissal
from routers.dismissals import active_dismissed_keys, router


def _engine():  # type: ignore[no-untyped-def]
    e = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(e)
    return e


@pytest.fixture()
def state():  # type: ignore[no-untyped-def]
    engine = _engine()

    def _override() -> Session:  # type: ignore[misc]
        with Session(engine) as session:
            yield session  # type: ignore[misc]

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override
    return engine, TestClient(app)


def test_dismiss_inserts_new_row(state) -> None:  # type: ignore[no-untyped-def]
    engine, c = state
    r = c.post("/api/patterns/dismiss", json={
        "product": "python", "main": "Technical", "sub": "Reliability", "days": 7,
        "reason": "fix shipping next week",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["product"] == "python"
    assert body["reason"] == "fix shipping next week"
    assert body["dismissed_until"] is not None


def test_dismiss_is_idempotent_per_key(state) -> None:  # type: ignore[no-untyped-def]
    engine, c = state
    r1 = c.post("/api/patterns/dismiss", json={
        "product": "python", "main": "M", "sub": "S", "days": 7,
    })
    r2 = c.post("/api/patterns/dismiss", json={
        "product": "python", "main": "M", "sub": "S", "days": 30,
    })
    assert r1.status_code == 200 and r2.status_code == 200
    # Same id — the second call updated the existing row.
    assert r1.json()["id"] == r2.json()["id"]


def test_dismiss_rejects_days_and_until_together(state) -> None:  # type: ignore[no-untyped-def]
    engine, c = state
    r = c.post("/api/patterns/dismiss", json={
        "product": "python", "main": "M", "sub": "S",
        "days": 7, "until": "2026-12-31T00:00:00",
    })
    assert r.status_code == 422


def test_restore_removes_dismissal(state) -> None:  # type: ignore[no-untyped-def]
    engine, c = state
    c.post("/api/patterns/dismiss", json={
        "product": "python", "main": "M", "sub": "S", "days": 7,
    })
    r = c.delete("/api/patterns/dismiss?product=python&main=M&sub=S")
    assert r.status_code == 204
    assert c.get("/api/patterns/dismiss?product=python").json() == []


def test_restore_is_noop_when_missing(state) -> None:  # type: ignore[no-untyped-def]
    engine, c = state
    r = c.delete("/api/patterns/dismiss?product=x&main=y&sub=z")
    assert r.status_code == 204


def test_list_filters_expired(state) -> None:  # type: ignore[no-untyped-def]
    engine, _ = state
    with Session(engine) as s:
        # One active (future), one expired (past), one indefinite (None).
        s.add(PatternDismissal(
            product_tag="python", main_category="A", sub_category="X",
            dismissed_until=utcnow() + timedelta(days=5),
        ))
        s.add(PatternDismissal(
            product_tag="python", main_category="B", sub_category="Y",
            dismissed_until=utcnow() - timedelta(days=1),
        ))
        s.add(PatternDismissal(
            product_tag="python", main_category="C", sub_category="Z",
            dismissed_until=None,
        ))
        s.commit()

    _, c = state
    active = c.get("/api/patterns/dismiss?product=python").json()
    assert {(d["main"], d["sub"]) for d in active} == {("A", "X"), ("C", "Z")}

    all_rows = c.get("/api/patterns/dismiss?product=python&include_expired=true").json()
    assert len(all_rows) == 3


def test_active_dismissed_keys_helper(state) -> None:  # type: ignore[no-untyped-def]
    engine, _ = state
    with Session(engine) as s:
        s.add(PatternDismissal(
            product_tag="python", main_category="A", sub_category="X",
            dismissed_until=utcnow() + timedelta(days=5),
        ))
        s.add(PatternDismissal(
            product_tag="other", main_category="Q", sub_category="W",
        ))
        s.commit()
        keys = active_dismissed_keys(s, "python")
    assert keys == {("A", "X")}
