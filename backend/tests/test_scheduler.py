"""
Tests for S10 — Scheduled refresh.

Covers:
  - routers/scheduler.py  (HTTP endpoint behaviour, validated via direct async calls)
  - services/scheduler.py  (SchedulerService._get_unclassified, _execute_run)

No Ollama, SO, or real filesystem access — all external calls are mocked.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

import routers.scheduler as sched_router
from app.models import Classification, Question, ScheduleConfig
from routers.scheduler import (
    ScheduleConfigRequest,
    get_schedule,
    get_status,
    set_schedule,
    set_scheduler,
    trigger_now,
)
from services.scheduler import SchedulerService

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """Fresh in-memory SQLite with all tables created."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture(autouse=True)
def patch_engine(engine: Any, monkeypatch: Any) -> None:
    """Make every router function use the test engine, not the real file-based one."""
    monkeypatch.setattr(sched_router, "app_engine", engine)


@pytest.fixture(autouse=True)
def reset_scheduler() -> None:
    """Ensure _scheduler is None between tests."""
    set_scheduler(None)
    yield
    set_scheduler(None)


# ── Helper ────────────────────────────────────────────────────────────────────


def _make_question(engine: Any, so_id: int, author_id: int = 1) -> Question:
    with Session(engine) as session:
        q = Question(
            so_id=so_id,
            title=f"Question {so_id}",
            body="body",
            tags=json.dumps([]),
            score=0,
            view_count=0,
            created_at=datetime.utcnow(),
            author_id=author_id,
            answer_count=0,
            has_accepted=False,
        )
        session.add(q)
        session.commit()
        session.refresh(q)
    return q


def _make_cls(engine: Any, question_id: int) -> None:
    with Session(engine) as session:
        session.add(Classification(
            question_id=question_id,
            main_category="Technical",
            sub_category="Reliability issues or instability",
            confidence=0.9,
            is_noise=False,
            model="llama3.1:8b",
        ))
        session.commit()


# ── GET /api/schedule ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_schedule_no_config_returns_defaults() -> None:
    result = await get_schedule()
    assert result.enabled is False
    assert result.interval_hours == 24
    assert result.products == []
    assert result.window_days == 30
    assert result.last_run_at is None
    assert result.next_run_at is None


@pytest.mark.asyncio
async def test_get_schedule_returns_persisted_values(engine: Any) -> None:
    with Session(engine) as session:
        session.add(ScheduleConfig(
            enabled=True,
            interval_hours=6,
            products='["python", "java"]',
            window_days=60,
        ))
        session.commit()

    result = await get_schedule()
    assert result.enabled is True
    assert result.interval_hours == 6
    assert result.products == ["python", "java"]
    assert result.window_days == 60


# ── POST /api/schedule ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_schedule_creates_row(engine: Any) -> None:
    body = ScheduleConfigRequest(enabled=True, interval_hours=12, products=["api"], window_days=30)
    result = await set_schedule(body)

    assert result.enabled is True
    assert result.interval_hours == 12
    assert result.products == ["api"]

    with Session(engine) as session:
        rows = session.exec(select(ScheduleConfig)).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_set_schedule_updates_without_duplicating(engine: Any) -> None:
    body1 = ScheduleConfigRequest(
        enabled=True, interval_hours=24, products=["api"], window_days=30
    )
    body2 = ScheduleConfigRequest(
        enabled=False, interval_hours=48, products=["docs"], window_days=60
    )
    await set_schedule(body1)
    await set_schedule(body2)

    with Session(engine) as session:
        rows = session.exec(select(ScheduleConfig)).all()
    assert len(rows) == 1
    assert rows[0].interval_hours == 48
    assert json.loads(rows[0].products) == ["docs"]


@pytest.mark.asyncio
async def test_set_schedule_enabled_sets_next_run_at(engine: Any) -> None:
    body = ScheduleConfigRequest(enabled=True, interval_hours=6, products=["p"], window_days=30)
    result = await set_schedule(body)
    assert result.next_run_at is not None
    # next_run_at should be roughly 6 hours from now
    assert result.next_run_at > datetime.utcnow()


@pytest.mark.asyncio
async def test_set_schedule_disabled_clears_next_run_at(engine: Any) -> None:
    # Enable first to set next_run_at
    body_on = ScheduleConfigRequest(enabled=True, interval_hours=6, products=["p"], window_days=30)
    await set_schedule(body_on)
    # Then disable
    body_off = ScheduleConfigRequest(
        enabled=False, interval_hours=6, products=["p"], window_days=30
    )
    result = await set_schedule(body_off)
    assert result.next_run_at is None


def test_schedule_config_request_rejects_zero_interval() -> None:
    with pytest.raises(ValidationError):
        ScheduleConfigRequest(enabled=True, interval_hours=0, products=[], window_days=30)


def test_schedule_config_request_rejects_zero_window() -> None:
    with pytest.raises(ValidationError):
        ScheduleConfigRequest(enabled=True, interval_hours=24, products=[], window_days=0)


# ── GET /api/schedule/status ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_status_no_config() -> None:
    result = await get_status()
    assert result.enabled is False
    assert result.running is False
    assert result.last_run_at is None


@pytest.mark.asyncio
async def test_get_status_reflects_scheduler_running(engine: Any) -> None:
    mock_svc = MagicMock()
    mock_svc.is_running.return_value = True
    set_scheduler(mock_svc)

    result = await get_status()
    assert result.running is True


# ── POST /api/schedule/trigger ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trigger_with_no_products_returns_400(engine: Any) -> None:
    with Session(engine) as session:
        session.add(ScheduleConfig(
            enabled=True, products="[]", interval_hours=24, window_days=30,
        ))
        session.commit()

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await trigger_now()
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_trigger_no_config_returns_400() -> None:
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await trigger_now()
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_trigger_no_scheduler_returns_503(engine: Any) -> None:
    with Session(engine) as session:
        session.add(ScheduleConfig(
            enabled=True, products='["p"]', interval_hours=24, window_days=30,
        ))
        session.commit()

    from fastapi import HTTPException
    # _scheduler is None (reset_scheduler fixture)
    with pytest.raises(HTTPException) as exc_info:
        await trigger_now()
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_trigger_returns_202_status(engine: Any) -> None:
    with Session(engine) as session:
        session.add(ScheduleConfig(
            enabled=True, products='["p"]', interval_hours=24, window_days=30,
        ))
        session.commit()

    mock_svc = MagicMock()
    mock_svc.trigger_now = AsyncMock()
    set_scheduler(mock_svc)

    result = await trigger_now()
    assert result == {"status": "triggered"}
    # Give the event loop one tick so the created task starts.
    await asyncio.sleep(0)
    mock_svc.trigger_now.assert_called_once()


# ── SchedulerService._get_unclassified ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_unclassified_returns_only_unclassified(engine: Any) -> None:
    q1 = _make_question(engine, so_id=1)
    q2 = _make_question(engine, so_id=2)
    q3 = _make_question(engine, so_id=3)
    assert q1.id is not None
    assert q2.id is not None
    _make_cls(engine, q1.id)
    _make_cls(engine, q2.id)
    # q3 has no classification

    svc = SchedulerService(engine=engine, ollama_url="http://localhost:11434")
    unclassified = svc._get_unclassified()

    assert len(unclassified) == 1
    assert unclassified[0].so_id == q3.so_id


@pytest.mark.asyncio
async def test_get_unclassified_all_classified_returns_empty(engine: Any) -> None:
    q = _make_question(engine, so_id=10)
    assert q.id is not None
    _make_cls(engine, q.id)

    svc = SchedulerService(engine=engine, ollama_url="http://localhost:11434")
    assert svc._get_unclassified() == []


@pytest.mark.asyncio
async def test_get_unclassified_no_questions_returns_empty(engine: Any) -> None:
    svc = SchedulerService(engine=engine, ollama_url="http://localhost:11434")
    assert svc._get_unclassified() == []


# ── SchedulerService._execute_run ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_run_updates_timestamps(engine: Any) -> None:
    with Session(engine) as session:
        cfg = ScheduleConfig(enabled=True, interval_hours=12, products='["p"]', window_days=30)
        session.add(cfg)
        session.commit()
        session.refresh(cfg)

    svc = SchedulerService(engine=engine, ollama_url="http://localhost:11434")

    with (
        patch.object(svc, "_ingest", new=AsyncMock()),
        patch.object(svc, "_classify", new=AsyncMock()),
        patch.object(svc, "_aggregate", new=AsyncMock()),
    ):
        with Session(engine) as session:
            config = session.exec(select(ScheduleConfig)).first()
            assert config is not None
        await svc._execute_run(config)

    with Session(engine) as session:
        updated = session.exec(select(ScheduleConfig)).first()
        assert updated is not None

    assert updated.last_run_at is not None
    assert updated.next_run_at is not None
    # next_run_at should be ≈ 12 hours after last_run_at
    delta = updated.next_run_at - updated.last_run_at
    assert abs(delta.total_seconds() - 12 * 3600) < 5  # within 5 s tolerance


@pytest.mark.asyncio
async def test_execute_run_calls_all_three_stages(engine: Any) -> None:
    with Session(engine) as session:
        cfg = ScheduleConfig(enabled=True, interval_hours=24, products='["p"]', window_days=30)
        session.add(cfg)
        session.commit()
        session.refresh(cfg)

    svc = SchedulerService(engine=engine, ollama_url="http://localhost:11434")
    ingest_mock = AsyncMock()
    classify_mock = AsyncMock()
    aggregate_mock = AsyncMock()

    with (
        patch.object(svc, "_ingest", new=ingest_mock),
        patch.object(svc, "_classify", new=classify_mock),
        patch.object(svc, "_aggregate", new=aggregate_mock),
    ):
        with Session(engine) as session:
            config = session.exec(select(ScheduleConfig)).first()
            assert config is not None
        await svc._execute_run(config)

    ingest_mock.assert_called_once_with(["p"], 30)
    classify_mock.assert_called_once()
    aggregate_mock.assert_called_once_with(["p"], 30)


@pytest.mark.asyncio
async def test_execute_run_skips_when_already_running(engine: Any) -> None:
    with Session(engine) as session:
        cfg = ScheduleConfig(enabled=True, interval_hours=24, products='["p"]', window_days=30)
        session.add(cfg)
        session.commit()
        session.refresh(cfg)

    svc = SchedulerService(engine=engine, ollama_url="http://localhost:11434")
    svc._currently_running = True  # simulate another run in progress

    ingest_mock = AsyncMock()
    with patch.object(svc, "_ingest", new=ingest_mock):
        with Session(engine) as session:
            config = session.exec(select(ScheduleConfig)).first()
            assert config is not None
        await svc._execute_run(config)

    ingest_mock.assert_not_called()


@pytest.mark.asyncio
async def test_execute_run_clears_running_flag_on_error(engine: Any) -> None:
    """_currently_running must be reset even when a stage raises."""
    with Session(engine) as session:
        cfg = ScheduleConfig(enabled=True, interval_hours=24, products='["p"]', window_days=30)
        session.add(cfg)
        session.commit()
        session.refresh(cfg)

    svc = SchedulerService(engine=engine, ollama_url="http://localhost:11434")

    async def _boom(*_: Any) -> None:
        raise RuntimeError("simulated failure")

    with patch.object(svc, "_ingest", new=_boom):
        with Session(engine) as session:
            config = session.exec(select(ScheduleConfig)).first()
            assert config is not None
        await svc._execute_run(config)

    assert svc._currently_running is False
