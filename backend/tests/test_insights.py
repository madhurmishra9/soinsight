"""
Tests for routers/insights.py — pure reads from SQLite.

Uses a seeded in-memory SQLite DB; no SO or Ollama calls.
TestClient drives HTTP so response shape is tested end-to-end.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from app.models import Classification, Pattern, Question
from routers.insights import (
    InsightsSummary,
    _render_markdown,
    get_session,
)
from routers.insights import (
    router as insights_router,
)

# "Now" for seeding — recent so questions pass a 30-day window filter
_RECENT = datetime.utcnow() - timedelta(days=5)
_OLD = datetime.utcnow() - timedelta(days=45)


# ─── Fixtures ─────────────────────────────────────────────────────────────────


def _make_engine() -> Any:
    # StaticPool: reuse the same in-memory connection across all sessions in this test.
    # Without it, each new Session gets a fresh empty `:memory:` database.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _make_question(
    so_id: int,
    tags: list[str],
    author_id: int = 1,
    created_at: datetime | None = None,
) -> Question:
    return Question(
        so_id=so_id,
        title=f"Question {so_id}",
        body="Body text.",
        tags=json.dumps(tags),
        score=0,
        view_count=10,
        created_at=created_at or _RECENT,
        author_id=author_id,
        answer_count=0,
        has_accepted=False,
    )


def _make_cls(
    question_id: int,
    main: str,
    sub: str,
    is_noise: bool = False,
) -> Classification:
    return Classification(
        question_id=question_id,
        main_category=main,
        sub_category=sub,
        confidence=0.9,
        is_noise=is_noise,
        model="llama3.1:8b",
    )


def _make_pattern(product_tag: str, window_days: int = 30) -> Pattern:
    return Pattern(
        product_tag=product_tag,
        window_days=window_days,
        main_category="Technical",
        sub_category="Reliability issues or instability",
        question_count=3,
        distinct_users=3,
        suggested_action="Fix or optimise",
        first_seen=_RECENT,
        last_seen=_RECENT,
        summary="3 questions about 'Reliability issues or instability' from 3 distinct users.",
    )


@pytest.fixture
def seeded_client() -> TestClient:
    """
    Seed data:
      python-tagged questions (recent):
        q1 author=1, tags=[python, docker]   → Technical / Reliability  (signal)
        q2 author=2, tags=[python]           → Technical / Reliability  (signal)
        q3 author=3, tags=[python, api]      → Technical / Reliability  (signal)
        q4 author=4, tags=[python]           → Documentation / Missing  (signal)
        q5 author=5, tags=[python]           → Misuse / Noise           (noise)
      q_old author=1, tags=[python], old     → (no classification — window filter)
      q_java author=6, tags=[java]           → (different product)

    Pattern row:
      Technical / Reliability, python, 30 days → suggested_action="Fix or optimise"
    """
    engine = _make_engine()

    with Session(engine) as s:
        qs = [
            _make_question(1, ["python", "docker"], author_id=1),
            _make_question(2, ["python"], author_id=2),
            _make_question(3, ["python", "api"], author_id=3),
            _make_question(4, ["python"], author_id=4),
            _make_question(5, ["python"], author_id=5),
            _make_question(6, ["python"], author_id=1, created_at=_OLD),   # old — outside window
            _make_question(7, ["java"], author_id=6),
        ]
        for q in qs:
            s.add(q)
        s.commit()
        for q in qs:
            s.refresh(q)

        # id lookup: so_id 1-5 are the recent python questions
        q_ids = {q.so_id: q.id for q in qs}

        classifications = [
            _make_cls(q_ids[1], "Technical", "Reliability issues or instability"),
            _make_cls(q_ids[2], "Technical", "Reliability issues or instability"),
            _make_cls(q_ids[3], "Technical", "Reliability issues or instability"),
            _make_cls(q_ids[4], "Documentation", "Missing Documentation"),
            _make_cls(q_ids[5], "Misuse / Noise", "Incorrect usage", is_noise=True),
        ]
        for c in classifications:
            s.add(c)

        s.add(_make_pattern("python", window_days=30))
        s.add(_make_pattern("python", window_days=60))  # second window for filter tests
        s.commit()

    app = FastAPI()
    app.include_router(insights_router)

    def override() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override
    return TestClient(app)


# ─── GET /api/insights/summary ────────────────────────────────────────────────


def test_summary_returns_200(seeded_client: TestClient) -> None:
    r = seeded_client.get("/api/insights/summary?product=python&window=30")
    assert r.status_code == 200


def test_summary_shape(seeded_client: TestClient) -> None:
    r = seeded_client.get("/api/insights/summary?product=python&window=30")
    data = r.json()
    assert data["product"] == "python"
    assert data["window_days"] == 30
    assert isinstance(data["total_questions"], int)
    assert isinstance(data["noise_count"], int)
    assert isinstance(data["category_breakdown"], list)
    assert isinstance(data["top_issues"], list)
    assert isinstance(data["patterns"], list)
    assert isinstance(data["recommended_actions"], list)


def test_summary_signal_counts(seeded_client: TestClient) -> None:
    data = seeded_client.get("/api/insights/summary?product=python&window=30").json()
    # 4 signal classifications (q1-q4); 1 noise (q5)
    assert data["total_questions"] == 4
    assert data["noise_count"] == 1


def test_summary_breakdown_sorted_descending(seeded_client: TestClient) -> None:
    data = seeded_client.get("/api/insights/summary?product=python&window=30").json()
    bd = data["category_breakdown"]
    assert len(bd) == 2
    # Technical/Reliability (3) before Documentation/Missing (1)
    assert bd[0]["main_category"] == "Technical"
    assert bd[0]["question_count"] == 3
    assert bd[1]["main_category"] == "Documentation"
    assert bd[1]["question_count"] == 1


def test_summary_top_issues_at_most_five(seeded_client: TestClient) -> None:
    data = seeded_client.get("/api/insights/summary?product=python&window=30").json()
    assert len(data["top_issues"]) <= 5
    # With only 2 categories, both appear
    assert len(data["top_issues"]) == 2


def test_summary_patterns_from_db(seeded_client: TestClient) -> None:
    data = seeded_client.get("/api/insights/summary?product=python&window=30").json()
    patterns = data["patterns"]
    assert len(patterns) == 1
    p = patterns[0]
    assert p["main_category"] == "Technical"
    assert p["sub_category"] == "Reliability issues or instability"
    assert p["question_count"] == 3
    assert p["suggested_action"] == "Fix or optimise"


def test_summary_recommended_actions_deduped(seeded_client: TestClient) -> None:
    data = seeded_client.get("/api/insights/summary?product=python&window=30").json()
    actions = data["recommended_actions"]
    # One pattern → one unique action
    assert actions == ["Fix or optimise"]
    # No duplicates
    assert len(actions) == len(set(actions))


def test_summary_technical_ratio_present(seeded_client: TestClient) -> None:
    data = seeded_client.get("/api/insights/summary?product=python&window=30").json()
    # All 5 recent python questions have "python" tag (a _TECHNICAL_TAG) → ratio = 1.0
    assert data["technical_ratio"] == 1.0


def test_summary_non_technical_ratio_is_complement(seeded_client: TestClient) -> None:
    data = seeded_client.get("/api/insights/summary?product=python&window=30").json()
    tech = data["technical_ratio"]
    non_tech = data["non_technical_ratio"]
    assert tech is not None and non_tech is not None
    assert abs(tech + non_tech - 1.0) < 1e-6


def test_summary_empty_product_returns_zeros(seeded_client: TestClient) -> None:
    data = seeded_client.get("/api/insights/summary?product=nonexistent&window=30").json()
    assert data["total_questions"] == 0
    assert data["noise_count"] == 0
    assert data["patterns"] == []
    assert data["category_breakdown"] == []
    assert data["technical_ratio"] is None
    assert data["non_technical_ratio"] is None


def test_summary_window_filters_old_questions(seeded_client: TestClient) -> None:
    # window=7 — q_old (45 days ago) excluded; 5 recent python questions included
    data = seeded_client.get("/api/insights/summary?product=python&window=7").json()
    # signal: 4 (same as 30-day since old question had no classification anyway)
    assert data["total_questions"] == 4


def test_summary_missing_product_param_returns_422(seeded_client: TestClient) -> None:
    r = seeded_client.get("/api/insights/summary?window=30")
    assert r.status_code == 422


# ─── GET /api/insights/patterns ───────────────────────────────────────────────


def test_patterns_no_filter_returns_all(seeded_client: TestClient) -> None:
    data = seeded_client.get("/api/insights/patterns").json()
    # Two patterns seeded: window=30 and window=60
    assert len(data) == 2


def test_patterns_filter_by_product(seeded_client: TestClient) -> None:
    data = seeded_client.get("/api/insights/patterns?product=python").json()
    assert len(data) == 2
    assert all(p["main_category"] == "Technical" for p in data)


def test_patterns_filter_by_window(seeded_client: TestClient) -> None:
    data = seeded_client.get("/api/insights/patterns?window=30").json()
    assert len(data) == 1
    assert data[0]["suggested_action"] == "Fix or optimise"


def test_patterns_filter_product_and_window(seeded_client: TestClient) -> None:
    data = seeded_client.get("/api/insights/patterns?product=python&window=60").json()
    assert len(data) == 1
    assert data[0]["question_count"] == 3


def test_patterns_item_shape(seeded_client: TestClient) -> None:
    data = seeded_client.get("/api/insights/patterns?window=30").json()
    p = data[0]
    assert "main_category" in p
    assert "sub_category" in p
    assert "question_count" in p
    assert "distinct_users" in p
    assert "suggested_action" in p


def test_patterns_unknown_product_returns_empty(seeded_client: TestClient) -> None:
    data = seeded_client.get("/api/insights/patterns?product=unknown").json()
    assert data == []


# ─── GET /api/insights/questions ──────────────────────────────────────────────


def test_questions_with_sub_returns_matching_questions(seeded_client: TestClient) -> None:
    r = seeded_client.get(
        "/api/insights/questions",
        params={
            "product": "python",
            "main": "Technical",
            "sub": "Reliability issues or instability",
            "window": 30,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 3
    assert {q["so_id"] for q in data} == {1, 2, 3}
    assert all(q["url"] for q in data)


def test_questions_without_sub_includes_all_subs_of_main(seeded_client: TestClient) -> None:
    with_sub = seeded_client.get(
        "/api/insights/questions",
        params={
            "product": "python",
            "main": "Technical",
            "sub": "Reliability issues or instability",
        },
    ).json()
    without_sub = seeded_client.get(
        "/api/insights/questions",
        params={"product": "python", "main": "Technical"},
    ).json()
    assert len(without_sub) >= len(with_sub)
    assert {q["so_id"] for q in with_sub}.issubset({q["so_id"] for q in without_sub})


def test_questions_unknown_category_returns_empty(seeded_client: TestClient) -> None:
    r = seeded_client.get(
        "/api/insights/questions",
        params={
            "product": "python",
            "main": "Adoption / Migration",
            "sub": "Difficulty getting started",
        },
    )
    assert r.status_code == 200
    assert r.json() == []


def test_questions_excludes_noise(seeded_client: TestClient) -> None:
    r = seeded_client.get(
        "/api/insights/questions",
        params={"product": "python", "main": "Misuse / Noise", "sub": "Incorrect usage"},
    )
    assert r.json() == []


# ─── GET /api/insights/report ─────────────────────────────────────────────────


def test_report_json_returns_200(seeded_client: TestClient) -> None:
    r = seeded_client.get("/api/insights/report?product=python&format=json")
    assert r.status_code == 200
    assert "application/json" in r.headers["content-type"]


def test_report_json_is_parseable_and_has_fields(seeded_client: TestClient) -> None:
    r = seeded_client.get("/api/insights/report?product=python&format=json")
    data = r.json()
    assert data["product"] == "python"
    assert data["total_questions"] == 4
    assert data["noise_count"] == 1
    assert len(data["patterns"]) == 1


def test_report_json_default_format(seeded_client: TestClient) -> None:
    r = seeded_client.get("/api/insights/report?product=python")
    assert r.status_code == 200
    assert "application/json" in r.headers["content-type"]


def test_report_md_returns_200(seeded_client: TestClient) -> None:
    r = seeded_client.get("/api/insights/report?product=python&format=md")
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]


def test_report_md_contains_product_name(seeded_client: TestClient) -> None:
    r = seeded_client.get("/api/insights/report?product=python&format=md")
    assert "python" in r.text


def test_report_md_contains_expected_sections(seeded_client: TestClient) -> None:
    r = seeded_client.get("/api/insights/report?product=python&format=md")
    md = r.text
    assert "## Summary" in md
    assert "## Category Breakdown" in md
    assert "## Key Patterns" in md
    assert "## Recommended Actions" in md


def test_report_md_recommended_action_present(seeded_client: TestClient) -> None:
    r = seeded_client.get("/api/insights/report?product=python&format=md")
    assert "Fix or optimise" in r.text


def test_report_invalid_format_returns_422(seeded_client: TestClient) -> None:
    r = seeded_client.get("/api/insights/report?product=python&format=xml")
    assert r.status_code == 422


def test_report_json_includes_questions_on_breakdown_and_patterns(
    seeded_client: TestClient,
) -> None:
    data = seeded_client.get("/api/insights/report?product=python&format=json").json()
    for item in data["category_breakdown"]:
        assert len(item["questions"]) == item["question_count"]
    for p in data["patterns"]:
        assert len(p["questions"]) == p["question_count"]


def test_report_md_has_all_questions_appendix(seeded_client: TestClient) -> None:
    md = seeded_client.get("/api/insights/report?product=python&format=md").text
    assert "## All Questions by Category" in md
    assert "Question 1" in md  # so_id 1 — Technical / Reliability


def test_summary_does_not_embed_questions(seeded_client: TestClient) -> None:
    data = seeded_client.get("/api/insights/summary?product=python&window=30").json()
    for item in data["category_breakdown"]:
        assert item["questions"] == []
    for p in data["patterns"]:
        assert p["questions"] == []


# ─── _render_markdown unit test ───────────────────────────────────────────────


def test_render_markdown_empty_summary() -> None:
    s = InsightsSummary(
        product="test",
        window_days=30,
        total_questions=0,
        noise_count=0,
        category_breakdown=[],
        top_issues=[],
        patterns=[],
        recommended_actions=[],
        technical_ratio=None,
        non_technical_ratio=None,
    )
    md = _render_markdown(s)
    assert "# SOInsight Report — test (30-day window)" in md
    assert "## Summary" in md
    # No breakdown/patterns sections when data is empty
    assert "## Category Breakdown" not in md


def test_render_markdown_includes_technical_ratio() -> None:
    s = InsightsSummary(
        product="demo",
        window_days=30,
        total_questions=5,
        noise_count=0,
        category_breakdown=[],
        top_issues=[],
        patterns=[],
        recommended_actions=[],
        technical_ratio=0.6,
        non_technical_ratio=0.4,
    )
    md = _render_markdown(s)
    assert "60.0%" in md
    assert "40.0%" in md
    assert "APPROXIMATE" in md


# ─── Report includes the grounded remediation guide ───────────────────────────

def _remediation_client() -> TestClient:
    """A client seeded with one signal question + a stored Remediation row."""
    from app.models import Answer, Remediation
    engine = _make_engine()
    with Session(engine) as s:
        q = _make_question(1, ["python"], author_id=1)
        s.add(q)
        s.commit()
        s.refresh(q)
        s.add(_make_cls(q.id, "Technical", "Reliability issues or instability"))
        s.add(Answer(
            so_id=900, question_so_id=1, body="Enable connection keepalive.",
            score=4, is_accepted=True, created_at=datetime.utcnow(),
        ))
        s.add(Remediation(
            product_tag="python", window_days=30,
            main_category="Technical", sub_category="Reliability issues or instability",
            question_count=3, distinct_users=2,
            root_cause="Idle connections reaped under load.",
            solution="Raise idle timeout and enable keepalive on the pool.",
            prevention="Document the recommended pool settings in onboarding.",
            confidence=0.85, grounded=True,
            evidence_question_so_ids=json.dumps([1]),
            evidence_answer_so_ids=json.dumps([900]),
            content_hash="h", model="test-model", generated_at=datetime.utcnow(),
        ))
        s.commit()

    app = FastAPI()
    app.include_router(insights_router)

    def override() -> Any:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override
    return TestClient(app)


def test_report_markdown_includes_remediation_guide() -> None:
    client = _remediation_client()
    md = client.get("/api/insights/report?product=python&window=30&format=md").text
    assert "## Remediation Guide" in md
    assert "Raise idle timeout and enable keepalive" in md
    assert "Prevent recurrence:" in md
    assert "Grounded in:" in md


def test_report_json_includes_remediations() -> None:
    client = _remediation_client()
    data = client.get("/api/insights/report?product=python&window=30&format=json").json()
    assert "remediations" in data
    assert len(data["remediations"]) == 1
    rem = data["remediations"][0]
    assert rem["grounded"] is True
    assert rem["evidence_questions"][0]["so_id"] == 1
    assert rem["evidence_answers"][0]["so_id"] == 900


def test_report_markdown_without_remediations_has_no_guide(seeded_client: TestClient) -> None:
    # The default seeded client has no Remediation rows.
    md = seeded_client.get("/api/insights/report?product=python&window=30&format=md").text
    assert "## Remediation Guide" not in md
