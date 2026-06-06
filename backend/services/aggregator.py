"""
Aggregation service: per product/tag, compute patterns from classified questions.

Author technical/non-technical classification is APPROXIMATE — based on question
tags heuristic, NOT verified user profiles. Always label this clearly in output.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.models import Classification, Pattern, Question, Run
from app.taxonomy import RECOMMENDATION_MATRIX

log = structlog.get_logger("soinsight.aggregator")

_NOISE_MAIN = "Misuse / Noise"

# Default thresholds — all overridable via AggregatorService constructor
MIN_PATTERN_QUESTIONS: int = 3
MIN_PATTERN_USERS: int = 2
MIN_TREND_VOLUME: int = 5

# Tags treated as technical signals for the author heuristic (APPROXIMATE)
_TECHNICAL_TAGS: frozenset[str] = frozenset({
    "python", "javascript", "typescript", "java", "go", "rust", "c#", "c++",
    "api", "rest", "graphql", "sql", "nosql",
    "docker", "kubernetes", "k8s", "terraform",
    "ci", "cd", "git", "github", "gitlab",
    "linux", "bash", "shell",
    "microservices", "architecture", "aws", "azure", "gcp",
})


# ─── Result types ─────────────────────────────────────────────────────────────

@dataclass
class CategoryCount:
    main_category: str
    sub_category: str
    question_count: int
    distinct_users: int


@dataclass
class PatternResult:
    product_tag: str
    window_days: int
    main_category: str
    sub_category: str
    question_count: int
    distinct_users: int
    suggested_action: str
    first_seen: datetime | None
    last_seen: datetime | None
    summary: str = ""


@dataclass
class AggregationResult:
    product_tag: str
    window_days: int
    total_questions: int           # signal (non-noise) only
    noise_count: int               # noise volume — reported but excluded from total
    category_distribution: list[CategoryCount]
    patterns: list[PatternResult]
    # APPROXIMATE — tag heuristic, not a verified user attribute
    technical_ratio: float | None
    trend: str | None              # "increasing" | "stable" | "decreasing" | None


# ─── Service ──────────────────────────────────────────────────────────────────

class AggregatorService:
    """
    Computes per-product/tag patterns and category distributions.

    Pattern threshold: ≥ min_pattern_questions questions from ≥ min_pattern_users
    distinct authors within the same (main_category, sub_category).

    Trend guard: trend direction is only reported when total non-noise question
    count is ≥ min_trend_volume. Below that threshold, trend is None — suppresses
    unreliable spike detection on low-volume data.

    Author technical/non-technical classification is APPROXIMATE throughout.
    """

    def __init__(
        self,
        chroma_store: Any | None = None,
        min_pattern_questions: int = MIN_PATTERN_QUESTIONS,
        min_pattern_users: int = MIN_PATTERN_USERS,
        min_trend_volume: int = MIN_TREND_VOLUME,
    ) -> None:
        self._chroma = chroma_store   # reserved for embedding sub-clustering (future)
        self._min_q = min_pattern_questions
        self._min_u = min_pattern_users
        self._min_trend = min_trend_volume

    # ── Public API ────────────────────────────────────────────────────────────

    async def run(
        self,
        products: list[str],
        window_days: int,
        engine: Engine,
        queue: asyncio.Queue[dict[str, Any] | None] | None = None,
    ) -> list[AggregationResult]:
        """
        Aggregate for each product/tag in the window. Persists patterns and run record.
        Pushes SSE-style dicts to queue; None sentinel signals stream completion.
        """
        since = datetime.utcnow() - timedelta(days=window_days)
        results: list[AggregationResult] = []

        with Session(engine) as session:
            run_rec = Run(
                products=json.dumps(products),
                window_days=window_days,
                status="running",
            )
            session.add(run_rec)
            session.commit()
            session.refresh(run_rec)
            run_id: int | None = run_rec.id

            for tag in products:
                if queue:
                    await queue.put({"type": "tag_start", "tag": tag})
                log.info("aggregation_tag_start", tag=tag, window_days=window_days)

                result = self._aggregate_tag(session, tag, since, window_days)
                results.append(result)
                self._persist_patterns(session, result)
                session.commit()

                if queue:
                    await queue.put({
                        "type": "tag_done",
                        "tag": tag,
                        "patterns": len(result.patterns),
                        "total": result.total_questions,
                        "noise": result.noise_count,
                    })
                log.info(
                    "aggregation_tag_done",
                    tag=tag,
                    patterns=len(result.patterns),
                    total=result.total_questions,
                    noise=result.noise_count,
                )

            if run_id is not None:
                run_upd = session.get(Run, run_id)
                if run_upd is not None:
                    run_upd.finished_at = datetime.utcnow()
                    run_upd.status = "done"
                    run_upd.counts = json.dumps({
                        "products": len(products),
                        "patterns": sum(len(r.patterns) for r in results),
                    })
                    session.add(run_upd)
                    session.commit()

        log.info(
            "aggregation_done",
            products=len(products),
            total_patterns=sum(len(r.patterns) for r in results),
        )
        if queue:
            await queue.put({"type": "done", "products": len(products)})
            await queue.put(None)

        return results

    # ── Tag-level aggregation ─────────────────────────────────────────────────

    def _aggregate_tag(
        self,
        session: Session,
        tag: str,
        since: datetime,
        window_days: int,
    ) -> AggregationResult:
        all_qs = session.exec(
            select(Question).where(Question.created_at >= since)
        ).all()
        questions = [q for q in all_qs if _question_has_tag(q, tag)]
        q_by_id: dict[int, Question] = {q.id: q for q in questions if q.id is not None}

        if not q_by_id:
            return AggregationResult(
                product_tag=tag, window_days=window_days,
                total_questions=0, noise_count=0,
                category_distribution=[], patterns=[],
                technical_ratio=None, trend=None,
            )

        classifications = session.exec(
            select(Classification).where(
                Classification.question_id.in_(list(q_by_id.keys()))  # type: ignore[attr-defined]
            )
        ).all()

        noise_cls = [c for c in classifications if c.is_noise]
        signal_cls = [c for c in classifications if not c.is_noise]

        return AggregationResult(
            product_tag=tag,
            window_days=window_days,
            total_questions=len(signal_cls),
            noise_count=len(noise_cls),
            category_distribution=self._compute_distribution(signal_cls, q_by_id),
            patterns=self._compute_patterns(signal_cls, q_by_id, tag, window_days),
            technical_ratio=_compute_technical_ratio(questions),
            trend=self._compute_trend(signal_cls, q_by_id, window_days, len(signal_cls)),
        )

    # ── Distribution ──────────────────────────────────────────────────────────

    def _compute_distribution(
        self,
        signal_cls: list[Classification],
        q_by_id: dict[int, Question],
    ) -> list[CategoryCount]:
        counts: dict[tuple[str, str], int] = defaultdict(int)
        users: dict[tuple[str, str], set[int]] = defaultdict(set)

        for c in signal_cls:
            key = (c.main_category, c.sub_category)
            counts[key] += 1
            q = q_by_id.get(c.question_id)
            if q is not None:
                users[key].add(q.author_id)

        return sorted(
            [
                CategoryCount(
                    main_category=main,
                    sub_category=sub,
                    question_count=counts[(main, sub)],
                    distinct_users=len(users[(main, sub)]),
                )
                for (main, sub) in counts
            ],
            key=lambda cc: cc.question_count,
            reverse=True,
        )

    # ── Pattern detection ──────────────────────────────────────────────────────

    def _compute_patterns(
        self,
        signal_cls: list[Classification],
        q_by_id: dict[int, Question],
        product_tag: str,
        window_days: int,
    ) -> list[PatternResult]:
        """
        Group signal classifications by (main_category, sub_category).
        A group qualifies as a pattern only if it has:
          ≥ min_pattern_questions questions  AND
          ≥ min_pattern_users distinct authors.
        """
        by_sub: dict[tuple[str, str], list[int]] = defaultdict(list)
        for c in signal_cls:
            by_sub[(c.main_category, c.sub_category)].append(c.question_id)

        patterns: list[PatternResult] = []

        for (main, sub), q_ids_in_group in by_sub.items():
            qs_in_group = [q_by_id[qid] for qid in q_ids_in_group if qid in q_by_id]
            question_count = len(q_ids_in_group)
            distinct_users = len({q.author_id for q in qs_in_group})

            if question_count < self._min_q or distinct_users < self._min_u:
                continue

            timestamps = [q.created_at for q in qs_in_group if q.created_at is not None]
            first_seen = min(timestamps) if timestamps else None
            last_seen = max(timestamps) if timestamps else None

            patterns.append(PatternResult(
                product_tag=product_tag,
                window_days=window_days,
                main_category=main,
                sub_category=sub,
                question_count=question_count,
                distinct_users=distinct_users,
                suggested_action=RECOMMENDATION_MATRIX.get(main, "Review and address"),
                first_seen=first_seen,
                last_seen=last_seen,
                summary=(
                    f"{question_count} questions about '{sub}' "
                    f"from {distinct_users} distinct users."
                ),
            ))

        return sorted(patterns, key=lambda p: p.question_count, reverse=True)

    # ── Trend (within-window half-split, guarded by min_trend_volume) ─────────

    def _compute_trend(
        self,
        signal_cls: list[Classification],
        q_by_id: dict[int, Question],
        window_days: int,
        total_count: int,
        _now: datetime | None = None,
    ) -> str | None:
        """
        Split window in half; compare second-half vs first-half signal question counts.
        Returns None when total_count < min_trend_volume (volume guard suppresses
        unreliable spike detection).
        _now is injectable for deterministic tests.
        """
        if total_count < self._min_trend:
            return None

        now = _now or datetime.utcnow()
        halfway = now - timedelta(days=window_days // 2)

        first_count = 0
        second_count = 0
        for c in signal_cls:
            q = q_by_id.get(c.question_id)
            if q is None or q.created_at is None:
                continue
            if q.created_at < halfway:
                first_count += 1
            else:
                second_count += 1

        if first_count == 0:
            return None

        ratio = second_count / first_count
        if ratio > 1.3:
            return "increasing"
        if ratio < 0.7:
            return "decreasing"
        return "stable"

    # ── Persistence ───────────────────────────────────────────────────────────

    def _persist_patterns(self, session: Session, result: AggregationResult) -> None:
        """Upsert patterns keyed by (product_tag, window_days, main_category, sub_category)."""
        for p in result.patterns:
            existing = session.exec(
                select(Pattern).where(
                    Pattern.product_tag == p.product_tag,
                    Pattern.window_days == p.window_days,
                    Pattern.main_category == p.main_category,
                    Pattern.sub_category == p.sub_category,
                )
            ).first()

            if existing is not None:
                existing.question_count = p.question_count
                existing.distinct_users = p.distinct_users
                existing.suggested_action = p.suggested_action
                existing.last_seen = p.last_seen
                existing.summary = p.summary
                session.add(existing)
            else:
                session.add(Pattern(
                    product_tag=p.product_tag,
                    window_days=p.window_days,
                    main_category=p.main_category,
                    sub_category=p.sub_category,
                    question_count=p.question_count,
                    distinct_users=p.distinct_users,
                    suggested_action=p.suggested_action,
                    first_seen=p.first_seen,
                    last_seen=p.last_seen,
                    summary=p.summary,
                ))


# ─── Module-level helpers (used by AggregatorService and tests) ───────────────

def _question_has_tag(question: Question, tag: str) -> bool:
    """Exact tag membership check on the JSON-encoded tags field."""
    try:
        tags: list[str] = json.loads(question.tags or "[]")
        return tag in tags
    except (json.JSONDecodeError, TypeError):
        return False


def _compute_technical_ratio(questions: list[Question]) -> float | None:
    """
    APPROXIMATE fraction of questions with at least one technical tag.
    Heuristic: based on question tags — NOT a verified user attribute.
    """
    if not questions:
        return None
    technical = sum(
        1 for q in questions
        if any(
            t.lower() in _TECHNICAL_TAGS
            for t in _safe_tags(q)
        )
    )
    return round(technical / len(questions), 3)


def _safe_tags(question: Question) -> list[str]:
    try:
        return list(json.loads(question.tags or "[]"))
    except (json.JSONDecodeError, TypeError):
        return []
