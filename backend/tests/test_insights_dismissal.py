"""F1 integration: dismissed patterns drop out of /api/insights/summary."""

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
from app.db import get_session as app_db_get_session
from app.models import Classification, Pattern, PatternDismissal, Question
from routers.dismissals import router as dismissals_router
from routers.insights import get_session as insights_get_session
from routers.insights import router as insights_router


def _seed(engine) -> None:  # type: ignore[no-untyped-def]
    now = utcnow() - timedelta(days=2)
    with Session(engine) as s:
        qs = [
            Question(
                so_id=100 + i, title=f"q{i}", body="b", tags=json.dumps(["python"]),
                score=0, view_count=0, created_at=now, author_id=10 + i,
                answer_count=0, has_accepted=False,
            )
            for i in range(3)
        ]
        for q in qs:
            s.add(q)
        s.commit()
        for q in qs:
            s.refresh(q)
        for q in qs:
            s.add(Classification(
                question_id=q.id, main_category="Technical",
                sub_category="Reliability", is_noise=False,
                confidence=0.9, model="m",
            ))
        s.add(Pattern(
            product_tag="python", window_days=30,
            main_category="Technical", sub_category="Reliability",
            question_count=3, distinct_users=3,
            suggested_action="fix", summary="hi",
            first_seen=now, last_seen=now,
        ))
        s.commit()


@pytest.fixture()
def client():  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    _seed(engine)

    def override() -> Any:
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(insights_router)
    app.include_router(dismissals_router)
    # Both routers reference get_session — insights has its own copy.
    app.dependency_overrides[insights_get_session] = override
    app.dependency_overrides[app_db_get_session] = override
    return TestClient(app), engine


def test_dismissed_pattern_hidden_from_summary(client) -> None:  # type: ignore[no-untyped-def]
    c, _ = client
    before = c.get("/api/insights/summary?product=python&window=30").json()
    assert any(p["main_category"] == "Technical" for p in before["patterns"])
    assert any(
        b["main_category"] == "Technical" for b in before["category_breakdown"]
    )

    r = c.post("/api/patterns/dismiss", json={
        "product": "python", "main": "Technical", "sub": "Reliability", "days": 7,
    })
    assert r.status_code == 200

    after = c.get("/api/insights/summary?product=python&window=30").json()
    assert after["patterns"] == []
    assert after["category_breakdown"] == []
    assert after["top_issues"] == []


def test_include_dismissed_brings_pattern_back(client) -> None:  # type: ignore[no-untyped-def]
    c, _ = client
    c.post("/api/patterns/dismiss", json={
        "product": "python", "main": "Technical", "sub": "Reliability", "days": 7,
    })
    r = c.get("/api/insights/summary?product=python&window=30&include_dismissed=true")
    data = r.json()
    assert len(data["patterns"]) == 1
    assert len(data["category_breakdown"]) == 1


def test_expired_dismissal_does_not_hide(client) -> None:  # type: ignore[no-untyped-def]
    c, engine = client
    with Session(engine) as s:
        s.add(PatternDismissal(
            product_tag="python", main_category="Technical",
            sub_category="Reliability",
            dismissed_until=utcnow() - timedelta(hours=1),
        ))
        s.commit()
    data = c.get("/api/insights/summary?product=python&window=30").json()
    assert len(data["patterns"]) == 1
