"""Tests for /api/runs — surfacing past ingest/aggregate runs to the UI."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.dates import utcnow
from app.db import get_session
from app.models import Run
from routers.runs import router


def _make_engine():  # type: ignore[no-untyped-def]
    # StaticPool keeps a single connection alive so :memory: data is shared
    # between the fixture's seed write and the endpoint's read.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def client():  # type: ignore[no-untyped-def]
    engine = _make_engine()

    def _override() -> Session:  # type: ignore[misc]
        with Session(engine) as session:
            yield session  # type: ignore[misc]

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    with Session(engine) as session:
        now = utcnow()
        session.add(Run(
            started_at=now - timedelta(minutes=5),
            finished_at=now - timedelta(minutes=4),
            products=json.dumps(["python", "java"]),
            window_days=30,
            status="done",
            counts=json.dumps({"inserted": 12, "skipped": 3, "errors": 0}),
        ))
        session.add(Run(
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=2) + timedelta(seconds=42),
            products=json.dumps(["javascript"]),
            window_days=7,
            status="partial",
            counts=json.dumps({"inserted": 1, "skipped": 0, "errors": 2}),
        ))
        session.add(Run(
            started_at=now - timedelta(hours=24),
            products=json.dumps(["go"]),
            window_days=30,
            status="running",
        ))
        session.commit()

    return TestClient(app)


def test_returns_runs_newest_first(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/runs")
    assert r.status_code == 200
    runs = r.json()
    assert len(runs) == 3
    ts = [run["started_at"] for run in runs]
    assert ts == sorted(ts, reverse=True), "expected newest-first ordering"


def test_includes_parsed_products_and_counts(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/runs")
    done = next(run for run in r.json() if run["status"] == "done")
    assert done["products"] == ["python", "java"]
    assert done["counts"]["inserted"] == 12
    assert done["counts"]["skipped"] == 3
    assert done["window_days"] == 30
    assert done["duration_seconds"] is not None and done["duration_seconds"] > 0


def test_running_run_has_no_duration(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/runs")
    running = next(run for run in r.json() if run["status"] == "running")
    assert running["finished_at"] is None
    assert running["duration_seconds"] is None


def test_status_filter(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/runs?status=partial")
    runs = r.json()
    assert len(runs) == 1
    assert runs[0]["status"] == "partial"


def test_pagination_via_limit_and_offset(client) -> None:  # type: ignore[no-untyped-def]
    page1 = client.get("/api/runs?limit=1&offset=0").json()
    page2 = client.get("/api/runs?limit=1&offset=1").json()
    assert len(page1) == 1 and len(page2) == 1
    assert page1[0]["id"] != page2[0]["id"]


def test_empty_db_returns_empty_list() -> None:
    engine = _make_engine()

    def _override() -> Session:  # type: ignore[misc]
        with Session(engine) as session:
            yield session  # type: ignore[misc]

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override
    c = TestClient(app)

    r = c.get("/api/runs")
    assert r.status_code == 200
    assert r.json() == []
