"""F3 — tag auto-discovery: /api/insights/tag-suggestions."""

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
from app.models import Question
from routers.insights import get_session, router
from routers.questions import _tag_index_cache


@pytest.fixture(autouse=True)
def _reset_cache():  # type: ignore[no-untyped-def]
    _tag_index_cache.clear()
    yield
    _tag_index_cache.clear()


def _engine():  # type: ignore[no-untyped-def]
    e = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(e)
    return e


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


def test_returns_empty_when_cache_is_empty(client) -> None:  # type: ignore[no-untyped-def]
    _, c = client
    r = c.get("/api/insights/tag-suggestions?tracked=python,java")
    assert r.status_code == 200
    assert r.json() == []


def test_surfaces_untracked_tags_with_enough_volume(client) -> None:  # type: ignore[no-untyped-def]
    _, c = client
    _tag_index_cache[""] = {
        "tags": {"python": 500, "kotlin": 120, "fortran": 4, "rust": 80},
        "at": utcnow(), "ok": True,
    }
    r = c.get(
        "/api/insights/tag-suggestions?tracked=python&min_instance_count=25"
    )
    suggestions = r.json()
    names = {s["tag"] for s in suggestions}
    assert "python" not in names           # tracked → excluded
    assert "fortran" not in names          # below min_instance_count
    assert {"kotlin", "rust"} <= names


def test_ranked_by_instance_count(client) -> None:  # type: ignore[no-untyped-def]
    _, c = client
    _tag_index_cache[""] = {
        "tags": {"a": 30, "b": 200, "c": 60}, "at": utcnow(), "ok": True,
    }
    r = c.get("/api/insights/tag-suggestions?tracked=&min_instance_count=25")
    names = [s["tag"] for s in r.json()]
    assert names == ["b", "c", "a"]


def test_reports_local_coverage(client) -> None:  # type: ignore[no-untyped-def]
    engine, c = client
    _tag_index_cache[""] = {
        "tags": {"kotlin": 100}, "at": utcnow(), "ok": True,
    }
    with Session(engine) as s:
        for i in range(7):
            s.add(Question(
                so_id=i, title="q", body="b",
                tags=json.dumps(["kotlin", "android"]),
                score=0, view_count=0,
                created_at=utcnow() - timedelta(days=1),
                author_id=1, answer_count=0, has_accepted=False,
            ))
        s.commit()

    r = c.get("/api/insights/tag-suggestions?tracked=&min_instance_count=25")
    item = next(s for s in r.json() if s["tag"] == "kotlin")
    assert item["local_count"] == 7
    assert item["instance_count"] == 100
    assert item["coverage_ratio"] == 0.07


def test_skips_unsuccessful_cache_entries(client) -> None:  # type: ignore[no-untyped-def]
    _, c = client
    _tag_index_cache["team-x"] = {
        "tags": {"flask": 80}, "at": utcnow(), "ok": False,
    }
    r = c.get("/api/insights/tag-suggestions?tracked=python")
    assert r.json() == []
