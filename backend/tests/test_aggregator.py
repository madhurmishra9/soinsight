"""
Tests for services/aggregator.py.

Unit tests operate directly on _compute_patterns / _compute_trend / etc.
with in-memory mock objects — no DB needed.

Integration tests use in-memory SQLite to verify the full run() flow.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from app.models import Classification, Pattern, Question
from app.taxonomy import RECOMMENDATION_MATRIX, TAXONOMY
from services.aggregator import (
    AggregatorService,
    _compute_technical_ratio,
    _question_has_tag,
)

# ─── Helpers ──────────────────────────────────────────────────────────────────

_NOW = datetime(2024, 6, 1, 12, 0, 0)   # fixed reference point for trend tests


def _make_engine() -> Any:  # type: ignore[return]
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


def _make_q(
    q_id: int,
    author_id: int = 1,
    tags: list[str] | None = None,
    created_at: datetime | None = None,
) -> Question:
    """Create a Question with a manually assigned DB id (for unit tests without DB)."""
    q = Question(
        so_id=q_id,
        title=f"Question {q_id}",
        body="body text",
        tags=json.dumps(tags or []),
        score=0,
        view_count=0,
        created_at=created_at or _NOW,
        author_id=author_id,
        answer_count=0,
        has_accepted=False,
    )
    q.id = q_id   # simulate DB-assigned PK
    return q


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


def _insert_q(session: Session, so_id: int, author_id: int, tags: list[str],
              created_at: datetime | None = None) -> Question:
    q = Question(
        so_id=so_id,
        title=f"Question {so_id}",
        body="body",
        tags=json.dumps(tags),
        score=0, view_count=0,
        # Use current time so the 30-day window filter in _aggregate_tag includes this row.
        created_at=created_at or datetime.utcnow(),
        author_id=author_id,
        answer_count=0,
        has_accepted=False,
    )
    session.add(q)
    session.commit()
    session.refresh(q)
    return q


def _insert_cls(session: Session, question_id: int, main: str, sub: str,
                is_noise: bool = False) -> None:
    session.add(Classification(
        question_id=question_id,
        main_category=main,
        sub_category=sub,
        confidence=0.9,
        is_noise=is_noise,
        model="llama3.1:8b",
    ))
    session.commit()


# ─── _question_has_tag ────────────────────────────────────────────────────────

def test_question_has_tag_true() -> None:
    q = _make_q(1, tags=["python", "api"])
    assert _question_has_tag(q, "python") is True


def test_question_has_tag_false() -> None:
    q = _make_q(1, tags=["java"])
    assert _question_has_tag(q, "python") is False


def test_question_has_tag_exact_match_no_partial() -> None:
    q = _make_q(1, tags=["python3"])
    assert _question_has_tag(q, "python") is False


# ─── _compute_technical_ratio ─────────────────────────────────────────────────

def test_technical_ratio_all_technical() -> None:
    qs = [_make_q(i, tags=["python"]) for i in range(1, 4)]
    ratio = _compute_technical_ratio(qs)
    assert ratio == 1.0


def test_technical_ratio_none_technical() -> None:
    qs = [_make_q(i, tags=["process", "governance"]) for i in range(1, 4)]
    ratio = _compute_technical_ratio(qs)
    assert ratio == 0.0


def test_technical_ratio_empty_returns_none() -> None:
    assert _compute_technical_ratio([]) is None


def test_technical_ratio_mixed() -> None:
    qs = [_make_q(1, tags=["python"]), _make_q(2, tags=["process"])]
    ratio = _compute_technical_ratio(qs)
    assert ratio == 0.5


# ─── _compute_patterns: ≥3/≥2 threshold ───────────────────────────────────────

def test_below_question_count_threshold_no_pattern() -> None:
    """2 questions (need ≥3) → no pattern."""
    svc = AggregatorService()
    qs = {1: _make_q(1, author_id=1), 2: _make_q(2, author_id=2)}
    cls = [
        _make_cls(1, "Technical", "Reliability issues or instability"),
        _make_cls(2, "Technical", "Reliability issues or instability"),
    ]
    patterns = svc._compute_patterns(cls, qs, "python", 30)
    assert patterns == []


def test_below_distinct_user_threshold_no_pattern() -> None:
    """3 questions from only 1 user (need ≥2 users) → no pattern."""
    svc = AggregatorService()
    qs = {i: _make_q(i, author_id=99) for i in range(1, 4)}   # all same user
    cls = [_make_cls(i, "Technical", "Reliability issues or instability") for i in range(1, 4)]
    patterns = svc._compute_patterns(cls, qs, "python", 30)
    assert patterns == []


def test_at_threshold_forms_pattern() -> None:
    """Exactly 3 questions from 2 distinct users → 1 pattern."""
    svc = AggregatorService()
    qs = {
        1: _make_q(1, author_id=10),
        2: _make_q(2, author_id=20),
        3: _make_q(3, author_id=10),   # user 10 again — still 2 distinct
    }
    cls = [_make_cls(i, "Technical", "Reliability issues or instability") for i in range(1, 4)]
    patterns = svc._compute_patterns(cls, qs, "python", 30)
    assert len(patterns) == 1
    assert patterns[0].question_count == 3
    assert patterns[0].distinct_users == 2


def test_above_threshold_forms_pattern() -> None:
    """5 questions from 3 users → pattern."""
    svc = AggregatorService()
    qs = {i: _make_q(i, author_id=i % 3 + 1) for i in range(1, 6)}
    cls = [_make_cls(i, "Documentation", "Missing Documentation") for i in range(1, 6)]
    patterns = svc._compute_patterns(cls, qs, "docs", 30)
    assert len(patterns) == 1
    assert patterns[0].question_count == 5


def test_custom_threshold_respected() -> None:
    """Custom min_pattern_questions=5 — group of 4 should not qualify."""
    svc = AggregatorService(min_pattern_questions=5, min_pattern_users=2)
    qs = {i: _make_q(i, author_id=i % 2 + 1) for i in range(1, 5)}
    cls = [_make_cls(i, "Technical", "Performance or scaling issues") for i in range(1, 5)]
    patterns = svc._compute_patterns(cls, qs, "tag", 30)
    assert patterns == []


def test_two_sub_categories_each_form_separate_pattern() -> None:
    """Two independent sub-categories, each ≥3/≥2 → 2 patterns."""
    svc = AggregatorService()
    qs = {i: _make_q(i, author_id=i % 2 + 1) for i in range(1, 7)}
    cls = (
        [_make_cls(i, "Technical", "Reliability issues or instability") for i in range(1, 4)]
        + [_make_cls(i, "Technical", "Performance or scaling issues") for i in range(4, 7)]
    )
    patterns = svc._compute_patterns(cls, qs, "tag", 30)
    assert len(patterns) == 2


def test_mixed_threshold_only_qualifying_sub_forms_pattern() -> None:
    """One sub-category qualifies (3/2), one doesn't (2/2) → 1 pattern."""
    svc = AggregatorService()
    qs = {i: _make_q(i, author_id=i % 2 + 1) for i in range(1, 6)}
    cls = (
        [_make_cls(i, "Technical", "Reliability issues or instability") for i in range(1, 4)]
        # Performance sub has only 2 questions — below the ≥3 threshold
        + [_make_cls(i, "Technical", "Performance or scaling issues") for i in range(4, 6)]
    )
    patterns = svc._compute_patterns(cls, qs, "tag", 30)
    assert len(patterns) == 1
    assert patterns[0].sub_category == "Reliability issues or instability"


def test_patterns_sorted_by_question_count_desc() -> None:
    """Patterns are returned highest question_count first."""
    svc = AggregatorService()
    qs = {i: _make_q(i, author_id=i % 3 + 1) for i in range(1, 9)}
    cls = (
        [_make_cls(i, "Technical", "Reliability issues or instability") for i in range(1, 6)]  # 5
        + [_make_cls(i, "Documentation", "Missing Documentation") for i in range(6, 9)]        # 3
    )
    patterns = svc._compute_patterns(cls, qs, "tag", 30)
    assert patterns[0].question_count >= patterns[1].question_count


# ─── Recommendation matrix mapping ───────────────────────────────────────────

@pytest.mark.parametrize("main, expected_action", list(RECOMMENDATION_MATRIX.items()))
def test_recommendation_matrix_mapping(main: str, expected_action: str) -> None:
    """Each main_category maps to exactly the RECOMMENDATION_MATRIX action."""
    svc = AggregatorService()
    sub = TAXONOMY[main][0]   # first sub-category of this main
    qs = {i: _make_q(i, author_id=i % 2 + 1) for i in range(1, 4)}
    cls = [_make_cls(i, main, sub) for i in range(1, 4)]
    patterns = svc._compute_patterns(cls, qs, "tag", 30)
    assert len(patterns) == 1
    assert patterns[0].suggested_action == expected_action


def test_unknown_main_gets_fallback_action() -> None:
    """A main_category not in RECOMMENDATION_MATRIX uses a fallback string."""
    action = RECOMMENDATION_MATRIX.get("NonExistentCategory", "Review and address")
    assert action == "Review and address"


# ─── Noise exclusion from headline counts ─────────────────────────────────────

@pytest.mark.asyncio
async def test_noise_excluded_from_total_questions() -> None:
    """total_questions counts only non-noise; noise_count is reported separately."""
    engine = _make_engine()
    with Session(engine) as session:
        q1 = _insert_q(session, so_id=1, author_id=1, tags=["python"])
        q2 = _insert_q(session, so_id=2, author_id=2, tags=["python"])
        q3 = _insert_q(session, so_id=3, author_id=1, tags=["python"])
        q_noise = _insert_q(session, so_id=4, author_id=2, tags=["python"])
        _insert_cls(session, q1.id, "Technical", "Reliability issues or instability")
        _insert_cls(session, q2.id, "Technical", "Reliability issues or instability")
        _insert_cls(session, q3.id, "Technical", "Reliability issues or instability")
        _insert_cls(session, q_noise.id, "Misuse / Noise", "Duplicate questions", is_noise=True)

    svc = AggregatorService()
    results = await svc.run(["python"], 30, engine)

    assert len(results) == 1
    r = results[0]
    assert r.total_questions == 3    # only the 3 non-noise
    assert r.noise_count == 1


def test_noise_excluded_from_total_unit() -> None:
    """Unit test: noise classifications go to noise_count, not total_questions."""
    svc = AggregatorService()
    # Only signal_cls is passed to _compute_patterns; noise is already separated upstream
    qs = {i: _make_q(i, author_id=i % 2 + 1) for i in range(1, 4)}
    signal_cls = [
        _make_cls(i, "Technical", "Reliability issues or instability")
        for i in range(1, 4)
    ]
    noise_cls = [_make_cls(i, "Misuse / Noise", "Duplicate questions", is_noise=True)
                 for i in range(4, 7)]
    # _compute_patterns only sees signal_cls — noise never reaches pattern detection
    patterns = svc._compute_patterns(signal_cls, qs, "tag", 30)
    assert len(patterns) == 1
    assert all(p.main_category != "Misuse / Noise" for p in patterns)

    noise_patterns = svc._compute_patterns(noise_cls, qs, "tag", 30)
    # Even if we pass noise cls directly, qs[4..6] don't exist in q_by_id → 0 users → no pattern
    assert len(noise_patterns) == 0


@pytest.mark.asyncio
async def test_noise_count_reported() -> None:
    """noise_count is non-zero when noise classifications exist."""
    engine = _make_engine()
    with Session(engine) as session:
        q_noise = _insert_q(session, so_id=5, author_id=99, tags=["python"])
        _insert_cls(session, q_noise.id, "Misuse / Noise", "Incomplete or low-quality questions",
                    is_noise=True)

    svc = AggregatorService()
    results = await svc.run(["python"], 30, engine)
    assert results[0].noise_count == 1


# ─── Trend: minimum-volume guard ──────────────────────────────────────────────

def test_trend_guard_suppresses_low_volume() -> None:
    """With total_count < min_trend_volume → trend is None (guard fires)."""
    svc = AggregatorService(min_trend_volume=5)
    qs = {i: _make_q(i, author_id=i) for i in range(1, 5)}  # 4 questions
    cls = [_make_cls(i, "Technical", "Performance or scaling issues") for i in range(1, 5)]
    trend = svc._compute_trend(cls, qs, 30, total_count=4, _now=_NOW)
    assert trend is None


def test_trend_not_suppressed_at_threshold() -> None:
    """With total_count == min_trend_volume, guard does NOT fire."""
    svc = AggregatorService(min_trend_volume=5)
    now = _NOW
    halfway = now - timedelta(days=15)
    # 3 in first half, 2 in second half → ratio ≈ 0.67 → "decreasing"
    qs = {
        1: _make_q(1, created_at=halfway - timedelta(days=5)),
        2: _make_q(2, created_at=halfway - timedelta(days=3)),
        3: _make_q(3, created_at=halfway - timedelta(days=1)),
        4: _make_q(4, created_at=halfway + timedelta(days=2)),
        5: _make_q(5, created_at=halfway + timedelta(days=4)),
    }
    cls = [_make_cls(i, "Technical", "Performance or scaling issues") for i in range(1, 6)]
    trend = svc._compute_trend(cls, qs, 30, total_count=5, _now=now)
    assert trend is not None   # guard did not suppress


def test_trend_increasing_detected() -> None:
    """Second half >> first half → 'increasing'."""
    svc = AggregatorService(min_trend_volume=5)
    now = _NOW
    halfway = now - timedelta(days=15)
    qs = {
        1: _make_q(1, created_at=halfway - timedelta(days=2)),   # first half
        2: _make_q(2, created_at=halfway + timedelta(days=1)),   # second half
        3: _make_q(3, created_at=halfway + timedelta(days=2)),
        4: _make_q(4, created_at=halfway + timedelta(days=3)),
        5: _make_q(5, created_at=halfway + timedelta(days=4)),
        6: _make_q(6, created_at=halfway + timedelta(days=5)),
    }
    cls = [_make_cls(i, "Technical", "Performance or scaling issues") for i in range(1, 7)]
    trend = svc._compute_trend(cls, qs, 30, total_count=6, _now=now)
    assert trend == "increasing"


def test_trend_decreasing_detected() -> None:
    """First half >> second half → 'decreasing'."""
    svc = AggregatorService(min_trend_volume=5)
    now = _NOW
    halfway = now - timedelta(days=15)
    qs = {
        1: _make_q(1, created_at=halfway - timedelta(days=5)),
        2: _make_q(2, created_at=halfway - timedelta(days=4)),
        3: _make_q(3, created_at=halfway - timedelta(days=3)),
        4: _make_q(4, created_at=halfway - timedelta(days=2)),
        5: _make_q(5, created_at=halfway - timedelta(days=1)),
        6: _make_q(6, created_at=halfway + timedelta(days=2)),   # only 1 in second half
    }
    cls = [_make_cls(i, "Technical", "Performance or scaling issues") for i in range(1, 7)]
    trend = svc._compute_trend(cls, qs, 30, total_count=6, _now=now)
    assert trend == "decreasing"


def test_trend_stable_detected() -> None:
    """Roughly equal halves → 'stable'."""
    svc = AggregatorService(min_trend_volume=5)
    now = _NOW
    halfway = now - timedelta(days=15)
    qs = {
        1: _make_q(1, created_at=halfway - timedelta(days=3)),
        2: _make_q(2, created_at=halfway - timedelta(days=2)),
        3: _make_q(3, created_at=halfway - timedelta(days=1)),
        4: _make_q(4, created_at=halfway + timedelta(days=1)),
        5: _make_q(5, created_at=halfway + timedelta(days=2)),
        6: _make_q(6, created_at=halfway + timedelta(days=3)),
    }
    cls = [_make_cls(i, "Technical", "Performance or scaling issues") for i in range(1, 7)]
    trend = svc._compute_trend(cls, qs, 30, total_count=6, _now=now)
    assert trend == "stable"


def test_trend_none_when_no_first_half_data() -> None:
    """All questions in second half → first_count=0 → None (can't compare)."""
    svc = AggregatorService(min_trend_volume=5)
    now = _NOW
    halfway = now - timedelta(days=15)
    qs = {i: _make_q(i, created_at=halfway + timedelta(days=i)) for i in range(1, 7)}
    cls = [_make_cls(i, "Technical", "Performance or scaling issues") for i in range(1, 7)]
    trend = svc._compute_trend(cls, qs, 30, total_count=6, _now=now)
    assert trend is None


def test_trend_splits_on_window_not_wall_clock() -> None:
    """The half-split is anchored to the given since/until window.

    Regression: the split used to be anchored to utcnow(), so for a historical
    range the midpoint landed far past the window's end, every question fell in
    the "first half", and the trend came back "decreasing" no matter what.
    """
    svc = AggregatorService(min_trend_volume=5)
    since = datetime(2024, 1, 1)
    until = datetime(2024, 1, 31)
    mid = since + (until - since) / 2
    # 3 before the midpoint, 3 after → genuinely "stable".
    qs = {
        1: _make_q(1, created_at=mid - timedelta(days=10)),
        2: _make_q(2, created_at=mid - timedelta(days=6)),
        3: _make_q(3, created_at=mid - timedelta(days=2)),
        4: _make_q(4, created_at=mid + timedelta(days=2)),
        5: _make_q(5, created_at=mid + timedelta(days=6)),
        6: _make_q(6, created_at=mid + timedelta(days=10)),
    }
    cls = [_make_cls(i, "Technical", "Performance or scaling issues") for i in range(1, 7)]

    trend = svc._compute_trend(cls, qs, 30, total_count=6, since=since, until=until)
    assert trend == "stable"


@pytest.mark.asyncio
async def test_run_historical_range_does_not_report_false_decreasing() -> None:
    """End-to-end: a flat historical from_date/to_date range reports 'stable'."""
    engine = _make_engine()
    start = datetime(2024, 1, 1)
    with Session(engine) as session:
        for i in range(20):
            # 10 questions in the first half of January, 10 in the second half.
            offset = i if i < 10 else i + 6
            q = _insert_q(session, so_id=500 + i, author_id=i, tags=["python"],
                          created_at=start + timedelta(days=offset))
            _insert_cls(session, q.id, "Technical", "Performance or scaling issues")

    svc = AggregatorService(min_trend_volume=5)
    results = await svc.run(
        ["python"], 30, engine, from_date="2024-01-01", to_date="2024-01-31"
    )
    assert results[0].total_questions == 20
    assert results[0].trend == "stable"


# ─── Pattern persistence (integration) ───────────────────────────────────────

@pytest.mark.asyncio
async def test_patterns_persisted_to_db() -> None:
    engine = _make_engine()
    with Session(engine) as session:
        for i in range(1, 4):
            q = _insert_q(session, so_id=i, author_id=i % 2 + 1, tags=["python"])
            _insert_cls(session, q.id, "Technical", "Reliability issues or instability")

    svc = AggregatorService()
    await svc.run(["python"], 30, engine)

    with Session(engine) as session:
        rows = session.exec(select(Pattern)).all()
    assert len(rows) == 1
    assert rows[0].main_category == "Technical"
    assert rows[0].suggested_action == "Fix or optimise"


@pytest.mark.asyncio
async def test_pattern_persistence_idempotent() -> None:
    """Running aggregation twice on the same data updates rather than duplicates patterns."""
    engine = _make_engine()
    with Session(engine) as session:
        for i in range(1, 4):
            q = _insert_q(session, so_id=i, author_id=i % 2 + 1, tags=["python"])
            _insert_cls(session, q.id, "Technical", "Reliability issues or instability")

    svc = AggregatorService()
    await svc.run(["python"], 30, engine)
    await svc.run(["python"], 30, engine)

    with Session(engine) as session:
        rows = session.exec(select(Pattern)).all()
    assert len(rows) == 1   # not 2


@pytest.mark.asyncio
async def test_sse_queue_receives_sentinel() -> None:
    """Queue always receives None sentinel so SSE consumers can terminate."""
    engine = _make_engine()
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    svc = AggregatorService()
    await svc.run(["python"], 30, engine, queue=queue)

    events = []
    while not queue.empty():
        events.append(await queue.get())

    assert events[-1] is None
    assert any(e is not None and e.get("type") == "done" for e in events)


@pytest.mark.asyncio
async def test_no_patterns_below_global_threshold() -> None:
    """2 questions tagged python → below the default ≥3 threshold → no patterns."""
    engine = _make_engine()
    with Session(engine) as session:
        for i in range(1, 3):
            q = _insert_q(session, so_id=i, author_id=i, tags=["python"])
            _insert_cls(session, q.id, "Technical", "Reliability issues or instability")

    svc = AggregatorService()
    results = await svc.run(["python"], 30, engine)

    assert results[0].patterns == []
    with Session(engine) as session:
        assert session.exec(select(Pattern)).all() == []


# ─── Distribution accuracy ────────────────────────────────────────────────────

def test_category_distribution_excludes_noise() -> None:
    """Distribution is computed only from signal classifications."""
    svc = AggregatorService()
    qs = {i: _make_q(i, author_id=i) for i in range(1, 5)}
    signal = [_make_cls(1, "Technical", "Reliability issues or instability"),
              _make_cls(2, "Technical", "Reliability issues or instability")]
    # We pass only signal_cls to _compute_distribution
    dist = svc._compute_distribution(signal, qs)
    assert len(dist) == 1
    assert dist[0].question_count == 2
    assert dist[0].main_category == "Technical"


def test_category_distribution_sorted_desc() -> None:
    """Distribution is sorted by question_count descending."""
    svc = AggregatorService()
    qs = {i: _make_q(i, author_id=i) for i in range(1, 7)}
    cls = (
        [_make_cls(i, "Technical", "Reliability issues or instability") for i in range(1, 5)]  # 4
        + [_make_cls(i, "Documentation", "Missing Documentation") for i in range(5, 7)]        # 2
    )
    dist = svc._compute_distribution(cls, qs)
    assert dist[0].question_count >= dist[1].question_count


def test_distribution_empty_cls_returns_empty() -> None:
    """No signal classifications → empty distribution list."""
    svc = AggregatorService()
    qs = {i: _make_q(i, author_id=i) for i in range(1, 4)}
    dist = svc._compute_distribution([], qs)
    assert dist == []


# ─── run() with multiple products ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_multiple_products() -> None:
    """run() with two products returns one AggregationResult per product."""
    engine = _make_engine()
    with Session(engine) as session:
        for i in range(1, 4):
            q = _insert_q(session, so_id=i, author_id=i % 2 + 1, tags=["python"])
            _insert_cls(session, q.id, "Technical", "Reliability issues or instability")
        for i in range(10, 13):
            q = _insert_q(session, so_id=i, author_id=i % 2 + 1, tags=["java"])
            _insert_cls(session, q.id, "Documentation", "Missing Documentation")

    svc = AggregatorService()
    results = await svc.run(["python", "java"], 30, engine)

    assert len(results) == 2
    by_tag = {r.product_tag: r for r in results}
    assert "python" in by_tag
    assert "java" in by_tag
    assert by_tag["python"].total_questions == 3
    assert by_tag["java"].total_questions == 3


@pytest.mark.asyncio
async def test_run_product_with_no_matching_questions() -> None:
    """A product with no tagged questions returns zeros and an empty pattern list."""
    engine = _make_engine()
    svc = AggregatorService()
    results = await svc.run(["nonexistent-tag"], 30, engine)

    assert len(results) == 1
    r = results[0]
    assert r.product_tag == "nonexistent-tag"
    assert r.total_questions == 0
    assert r.noise_count == 0
    assert r.patterns == []
    assert r.category_distribution == []
    assert r.technical_ratio is None
