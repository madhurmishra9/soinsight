"""
Insights API — pure reads from SQLite.

All endpoints query the local DB only; no SO or Ollama calls.
technical_ratio is APPROXIMATE (question-tag heuristic, not a verified user attribute).
"""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import datetime, timedelta
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.dates import resolve_range, utcnow
from app.db import engine as app_engine
from app.models import Answer, Classification, Pattern, Question, Remediation
from app.settings import settings as app_settings
from routers.dismissals import active_dismissed_keys
from routers.settings import _current_config
from services.aggregator import _compute_technical_ratio, _question_has_tag, _safe_tags

log = structlog.get_logger("soinsight.routers.insights")

router = APIRouter(prefix="/api/insights", tags=["insights"])

_NOISE_MAIN = "Misuse / Noise"


# ─── Session dependency ────────────────────────────────────────────────────────

def get_session() -> Generator[Session, None, None]:
    with Session(app_engine) as session:
        yield session


# ─── Response models ───────────────────────────────────────────────────────────

class AnswerRef(BaseModel):
    so_id: int
    body: str
    score: int
    is_accepted: bool
    created_at: datetime


class QuestionRef(BaseModel):
    so_id: int
    title: str
    score: int
    view_count: int
    created_at: datetime
    url: str | None = None
    answer_count: int = 0
    answers: list[AnswerRef] = Field(default_factory=list)


class CategoryBreakdownItem(BaseModel):
    main_category: str
    sub_category: str
    question_count: int
    distinct_users: int
    questions: list[QuestionRef] = Field(default_factory=list)


class PatternItem(BaseModel):
    main_category: str
    sub_category: str
    question_count: int
    distinct_users: int
    suggested_action: str | None
    first_seen: datetime | None
    last_seen: datetime | None
    summary: str | None
    questions: list[QuestionRef] = Field(default_factory=list)


class InsightsSummary(BaseModel):
    product: str
    window_days: int
    total_questions: int                   # signal (non-noise) only
    noise_count: int                       # reported but excluded from total
    category_breakdown: list[CategoryBreakdownItem]
    top_issues: list[CategoryBreakdownItem]   # top 5 by question count
    patterns: list[PatternItem]
    recommended_actions: list[str]         # unique, in pattern-frequency order
    # APPROXIMATE — question-tag heuristic; never a verified user attribute
    noise_questions: list[QuestionRef] = []
    technical_ratio: float | None
    non_technical_ratio: float | None


# ─── Question lookups (shared by /questions, /summary, /report) ───────────────

def _question_url(so_id: int) -> str | None:
    """Reconstruct the SO Enterprise question URL from the configured base URL."""
    base = _current_config.get("base_url") or app_settings.so_base_url
    if not base:
        return None
    site = base.split("/api/")[0].rstrip("/")
    if not site:
        return None
    return f"{site}/questions/{so_id}"


def _answers_for(question_so_id: int, session: Session) -> list[AnswerRef]:
    """Load stored answers for a question, accepted first then by score."""
    rows = session.exec(
        select(Answer).where(Answer.question_so_id == question_so_id)
    ).all()
    refs = [
        AnswerRef(
            so_id=a.so_id,
            body=a.body,
            score=a.score,
            is_accepted=a.is_accepted,
            created_at=a.created_at,
        )
        for a in rows
    ]
    refs.sort(key=lambda x: (not x.is_accepted, -x.score))
    return refs


def _question_ref(
    q: Question, session: Session, include_answers: bool = False
) -> QuestionRef:
    """Build a QuestionRef, optionally hydrating its stored answers."""
    return QuestionRef(
        so_id=q.so_id,
        title=q.title,
        score=q.score,
        view_count=q.view_count,
        created_at=q.created_at,
        url=_question_url(q.so_id),
        answer_count=q.answer_count,
        answers=_answers_for(q.so_id, session) if include_answers else [],
    )


def _questions_by_category(
    product: str, window_days: int, session: Session,
    from_date: str | None = None, to_date: str | None = None,
    include_answers: bool = False,
) -> dict[tuple[str, str], list[QuestionRef]]:
    """Group every signal (non-noise) question for a product/window by (main, sub)."""
    since, until = resolve_range(window_days, from_date, to_date)
    all_qs = session.exec(
        select(Question).where(Question.created_at >= since, Question.created_at <= until)
    ).all()
    questions = [q for q in all_qs if _question_has_tag(q, product)]
    q_by_id: dict[int, Question] = {q.id: q for q in questions if q.id is not None}
    if not q_by_id:
        return {}

    cls = session.exec(
        select(Classification).where(
            Classification.question_id.in_(list(q_by_id.keys())),  # type: ignore[attr-defined]
            Classification.is_noise == False,  # noqa: E712
        )
    ).all()

    grouped: dict[tuple[str, str], list[QuestionRef]] = {}
    for c in cls:
        q = q_by_id.get(c.question_id)
        if q is None:
            continue
        grouped.setdefault((c.main_category, c.sub_category), []).append(
            _question_ref(q, session, include_answers=include_answers)
        )

    for key in grouped:
        grouped[key].sort(key=lambda x: x.score, reverse=True)
    return grouped


def _questions_for(
    product: str,
    window_days: int,
    main: str,
    sub: str | None,
    session: Session,
    from_date: str | None = None,
    to_date: str | None = None,
    noise: bool = False,
    include_answers: bool = False,
) -> list[QuestionRef]:
    """Questions behind a category, or noise questions when noise=True."""
    if noise:
        since, until = resolve_range(window_days, from_date, to_date)
        all_qs = session.exec(
            select(Question).where(Question.created_at >= since, Question.created_at <= until)
        ).all()
        questions = [q for q in all_qs if _question_has_tag(q, product)]
        q_by_id = {q.id: q for q in questions if q.id is not None}
        if not q_by_id:
            return []
        noise_cls = session.exec(
            select(Classification).where(
                Classification.question_id.in_(list(q_by_id.keys())),  # type: ignore[attr-defined]
                Classification.is_noise == True,  # noqa: E712
            )
        ).all()
        out = []
        for c in noise_cls:
            q = q_by_id.get(c.question_id)
            if q:
                out.append(_question_ref(q, session, include_answers=include_answers))
        return sorted(out, key=lambda x: x.score, reverse=True)

    grouped = _questions_by_category(
        product, window_days, session, from_date, to_date, include_answers=include_answers
    )
    if sub:
        return grouped.get((main, sub), [])

    out2: list[QuestionRef] = []
    for (m, _s), qs in grouped.items():
        if m == main:
            out2.extend(qs)
    return sorted(out2, key=lambda x: x.score, reverse=True)


# ─── Core summary builder (shared by /summary and /report) ────────────────────

def _build_summary(
    product: str, window_days: int, session: Session,
    include_questions: bool = False,
    from_date: str | None = None, to_date: str | None = None,
    include_dismissed: bool = False,
) -> InsightsSummary:
    since, until = resolve_range(window_days, from_date, to_date)
    dismissed = set() if include_dismissed else active_dismissed_keys(session, product)

    all_qs = session.exec(
        select(Question).where(Question.created_at >= since, Question.created_at <= until)
    ).all()
    questions = [q for q in all_qs if _question_has_tag(q, product)]
    q_by_id: dict[int, Question] = {q.id: q for q in questions if q.id is not None}

    if not q_by_id:
        return InsightsSummary(
            product=product,
            window_days=window_days,
            total_questions=0,
            noise_count=0,
            category_breakdown=[],
            top_issues=[],
            patterns=[],
            recommended_actions=[],
            technical_ratio=None,
            non_technical_ratio=None,
        )

    classifications = session.exec(
        select(Classification).where(
            Classification.question_id.in_(list(q_by_id.keys()))  # type: ignore[attr-defined]
        )
    ).all()

    noise_cls = [c for c in classifications if c.is_noise]
    signal_cls = [c for c in classifications if not c.is_noise]

    # Category breakdown — signal only, sorted descending by count
    counts: dict[tuple[str, str], int] = {}
    users: dict[tuple[str, str], set[int]] = {}
    for c in signal_cls:
        key = (c.main_category, c.sub_category)
        counts[key] = counts.get(key, 0) + 1
        q = q_by_id.get(c.question_id)
        if q is not None:
            users.setdefault(key, set()).add(q.author_id)

    grouped = (
        _questions_by_category(
            product, window_days, session, from_date, to_date,
            include_answers=include_questions,
        )
        if include_questions
        else {}
    )
    noise_qs: list[QuestionRef] = []
    if include_questions:
        for c in noise_cls:
            q = q_by_id.get(c.question_id)
            if q:
                noise_qs.append(_question_ref(q, session, include_answers=True))
        noise_qs.sort(key=lambda x: x.score, reverse=True)

    breakdown = sorted(
        [
            CategoryBreakdownItem(
                main_category=main,
                sub_category=sub,
                question_count=counts[(main, sub)],
                distinct_users=len(users.get((main, sub), set())),
                questions=grouped.get((main, sub), []),
            )
            for (main, sub) in counts
            if (main, sub) not in dismissed
        ],
        key=lambda x: x.question_count,
        reverse=True,
    )

    # Patterns from DB — already persisted by aggregator
    db_patterns = session.exec(
        select(Pattern).where(
            Pattern.product_tag == product,
            Pattern.window_days == window_days,
        )
    ).all()

    pattern_items = sorted(
        [
            PatternItem(
                main_category=p.main_category,
                sub_category=p.sub_category,
                question_count=p.question_count,
                distinct_users=p.distinct_users,
                suggested_action=p.suggested_action,
                first_seen=p.first_seen,
                last_seen=p.last_seen,
                summary=p.summary,
                questions=grouped.get((p.main_category, p.sub_category), []),
            )
            for p in db_patterns
            if (p.main_category, p.sub_category) not in dismissed
        ],
        key=lambda pi: pi.question_count,
        reverse=True,
    )

    # Deduplicated recommended actions, ordered by pattern frequency
    seen_actions: set[str] = set()
    recommended_actions: list[str] = []
    for pi in pattern_items:
        if pi.suggested_action and pi.suggested_action not in seen_actions:
            seen_actions.add(pi.suggested_action)
            recommended_actions.append(pi.suggested_action)

    tech_ratio = _compute_technical_ratio(list(questions))
    non_tech_ratio = round(1.0 - tech_ratio, 3) if tech_ratio is not None else None

    log.info(
        "insights_summary_built",
        product=product,
        window_days=window_days,
        total=len(signal_cls),
        noise=len(noise_cls),
        patterns=len(pattern_items),
    )

    return InsightsSummary(
        product=product,
        window_days=window_days,
        total_questions=len(signal_cls),
        noise_count=len(noise_cls),
        noise_questions=noise_qs,
        category_breakdown=breakdown,
        top_issues=breakdown[:5],
        patterns=pattern_items,
        recommended_actions=recommended_actions,
        technical_ratio=tech_ratio,
        non_technical_ratio=non_tech_ratio,
    )


# ─── Markdown renderer ─────────────────────────────────────────────────────────

def _truncate(text: str, limit: int = 500) -> str:
    """Collapse whitespace and clip long answer bodies for the report."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip() + "…"


def _q_lines(questions: list[QuestionRef], indent: str = "   ") -> list[str]:
    """Render question references as linked (or titled) bullet lines.

    When a question carries hydrated answers, each answer is rendered as a nested
    bullet beneath it (accepted answers flagged, body trimmed for readability).
    """
    out: list[str] = []
    for q in questions:
        link = f"[{q.title}]({q.url})" if q.url else q.title
        suffix = f" [Q#{q.so_id}] — score {q.score}, {q.view_count} views"
        if q.answer_count:
            suffix += f", {q.answer_count} answers"
        out.append(f"{indent}- {link}{suffix}")
        for a in q.answers:
            tag = "✓ accepted" if a.is_accepted else f"score {a.score}"
            out.append(f"{indent}  - _[A#{a.so_id}] ({tag})_ {_truncate(a.body)}")
    return out


def _render_markdown(s: InsightsSummary) -> str:
    now_str = utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        f"# SOInsight Report — {s.product} ({s.window_days}-day window)",
        "",
        f"_Generated: {now_str}_",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Total signal questions | {s.total_questions} |",
        f"| Noise volume | {s.noise_count} |",
        f"| Patterns detected | {len(s.patterns)} |",
    ]

    if s.technical_ratio is not None:
        tech_pct = round(s.technical_ratio * 100, 1)
        non_pct = round((s.non_technical_ratio or 0.0) * 100, 1)
        lines.append(
            f"| Technical / Non-technical (APPROXIMATE) | {tech_pct}% / {non_pct}% |"
        )

    if s.category_breakdown:
        lines += [
            "",
            "## Category Breakdown",
            "",
            "| Main Category | Sub-category | Questions | Distinct Users |",
            "|---|---|---|---|",
        ]
        for item in s.category_breakdown:
            lines.append(
                f"| {item.main_category} | {item.sub_category}"
                f" | {item.question_count} | {item.distinct_users} |"
            )

    if s.top_issues:
        lines += ["", "## Top Issues", ""]
        for i, item in enumerate(s.top_issues, 1):
            lines.append(
                f"{i}. **{item.main_category} / {item.sub_category}**"
                f" — {item.question_count} questions from {item.distinct_users} users"
            )
            lines += _q_lines(item.questions)
            lines.append("")

    if s.patterns:
        lines += ["", "## Key Patterns", ""]
        for p in s.patterns:
            lines += [
                f"### {p.main_category}: {p.sub_category}",
                "",
                f"- **Questions:** {p.question_count}",
                f"- **Distinct users:** {p.distinct_users}",
            ]
            if p.suggested_action:
                lines.append(f"- **Recommended action:** {p.suggested_action}")
            if p.summary:
                lines.append(f"- **Summary:** {p.summary}")
            lines.append("")
            lines += _q_lines(p.questions, indent="")
            lines.append("")

    if s.noise_questions:
        lines += ["", "## Noise / Excluded Questions",
                   "_These questions were classified as low-quality, duplicate, or off-topic.", ""]
        lines += _q_lines(s.noise_questions, indent="")
        lines.append("")

    if s.recommended_actions:
        lines += ["## Recommended Actions", ""]
        for i, action in enumerate(s.recommended_actions, 1):
            lines.append(f"{i}. {action}")
        lines.append("")

    if s.category_breakdown:
        lines += ["", "## All Questions by Category", ""]
        for item in s.category_breakdown:
            lines.append(
                f"### {item.main_category} / {item.sub_category}"
                f" ({item.question_count} questions, {item.distinct_users} users)"
            )
            lines += _q_lines(item.questions, indent="")
            lines.append("")

    return "\n".join(lines) + "\n"


def _remediation_export(product: str, window: int, session: Session) -> list[dict]:
    """Hydrated remediation records for export (grounded first), evidence resolved."""
    rows = session.exec(
        select(Remediation).where(
            Remediation.product_tag == product,
            Remediation.window_days == window,
        )
    ).all()
    rows = sorted(rows, key=lambda r: (not r.grounded, -r.question_count))

    items: list[dict] = []
    for r in rows:
        try:
            q_ids = [int(x) for x in json.loads(r.evidence_question_so_ids or "[]")]
        except (json.JSONDecodeError, ValueError, TypeError):
            q_ids = []
        try:
            a_ids = [int(x) for x in json.loads(r.evidence_answer_so_ids or "[]")]
        except (json.JSONDecodeError, ValueError, TypeError):
            a_ids = []

        ev_q: list[dict] = []
        if q_ids:
            qrows = session.exec(
                select(Question).where(col(Question.so_id).in_(q_ids))
            ).all()
            by_q = {q.so_id: q for q in qrows}
            for sid in q_ids:
                q = by_q.get(sid)
                if q:
                    ev_q.append({"so_id": q.so_id, "title": q.title, "url": _question_url(q.so_id)})

        ev_a: list[dict] = []
        if a_ids:
            arows = session.exec(
                select(Answer).where(col(Answer.so_id).in_(a_ids))
            ).all()
            by_a = {a.so_id: a for a in arows}
            for sid in a_ids:
                a = by_a.get(sid)
                if a:
                    body = " ".join((a.body or "").split())
                    ev_a.append({
                        "so_id": a.so_id, "question_so_id": a.question_so_id,
                        "is_accepted": a.is_accepted, "score": a.score,
                        "snippet": body[:300] + ("…" if len(body) > 300 else ""),
                    })

        items.append({
            "main_category": r.main_category,
            "sub_category": r.sub_category,
            "question_count": r.question_count,
            "distinct_users": r.distinct_users,
            "grounded": r.grounded,
            "confidence": r.confidence,
            "root_cause": r.root_cause,
            "solution": r.solution,
            "prevention": r.prevention,
            "model": r.model,
            "generated_at": r.generated_at.isoformat(),
            "evidence_questions": ev_q,
            "evidence_answers": ev_a,
        })
    return items


def _remediation_md(items: list[dict]) -> list[str]:
    """Render hydrated remediation records as a Markdown section."""
    if not items:
        return []
    lines: list[str] = [
        "",
        "## Remediation Guide (grounded fixes)",
        "",
        "_Detailed, source-grounded fixes for clusters of similar questions, so the "
        "same questions stop recurring. Every claim is tied to the cited sources below._",
        "",
    ]
    for it in items:
        lines.append(f"### {it['main_category']}: {it['sub_category']}")
        flag = (
            f"grounded · {round(it['confidence'] * 100)}% confidence"
            if it["grounded"] else "NOT grounded"
        )
        lines.append(
            f"_{it['question_count']} questions · {it['distinct_users']} users · {flag}_"
        )
        lines.append("")
        if not it["grounded"]:
            lines += [it["prevention"], ""]
            continue
        if it["root_cause"]:
            lines += [f"**Root cause:** {it['root_cause']}", ""]
        if it["solution"]:
            lines += [f"**Solution:** {it['solution']}", ""]
        if it["prevention"]:
            lines += [f"**Prevent recurrence:** {it['prevention']}", ""]
        if it["evidence_questions"]:
            lines.append("**Grounded in:**")
            for q in it["evidence_questions"]:
                link = f"[{q['title']}]({q['url']})" if q["url"] else q["title"]
                lines.append(f"- {link} [Q#{q['so_id']}]")
            lines.append("")
        for a in it["evidence_answers"]:
            tag = "accepted" if a["is_accepted"] else f"score {a['score']}"
            lines.append(
                f"  - _[A#{a['so_id']}] answer to [Q#{a['question_so_id']}]"
                f" ({tag}):_ {a['snippet']}"
            )
        if it["evidence_answers"]:
            lines.append("")
    return lines


# ─── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/summary", response_model=InsightsSummary)
async def get_summary(
    product: str = Query(..., description="Product/tag to summarise"),
    window: int = Query(30, ge=1, le=365, description="Window in days"),
    from_date: str | None = Query(None, description="YYYY-MM-DD — overrides window"),
    to_date: str | None = Query(None, description="YYYY-MM-DD — overrides window"),
    include_dismissed: bool = Query(False, description="Include snoozed patterns"),
    session: Session = Depends(get_session),
) -> InsightsSummary:
    """One summary per product/tag: category breakdown, patterns, recommendations."""
    return _build_summary(
        product, window, session,
        from_date=from_date, to_date=to_date,
        include_dismissed=include_dismissed,
    )


class TagSuggestion(BaseModel):
    tag: str
    instance_count: int        # tag's total questions on the SO instance
    local_count: int           # how many of that tag we already track
    coverage_ratio: float      # local_count / max(instance_count, 1)


@router.get("/tag-suggestions", response_model=list[TagSuggestion])
async def get_tag_suggestions(
    tracked: str = Query(
        ...,
        description="Comma-separated list of tags you already track",
    ),
    min_instance_count: int = Query(
        25, ge=1, description="Hide tags with fewer than this many instance-wide questions",
    ),
    limit: int = Query(20, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[TagSuggestion]:
    """Surface SO Enterprise tags worth tracking that aren't in your `tracked` list.

    Reads the cached instance tag index (populated by /api/questions/validate-tags
    or any prior tag fetch). Ranked by the instance tag's question count.
    """
    # Imported lazily to avoid the routers.questions ↔ routers.insights cycle
    # that a top-level import would create.
    from routers.questions import _tag_index_cache  # noqa: PLC0415

    tracked_set = {t.strip().lower() for t in tracked.split(",") if t.strip()}

    # Best-effort: scan ALL cached tag indexes (per team) and pick the freshest
    # successful one. The cache is a dict[team_key, {"tags": {name: count}, ...}].
    best: dict[str, int] = {}
    for entry in _tag_index_cache.values():
        if not entry.get("ok"):
            continue
        for name, count in entry.get("tags", {}).items():
            if count > best.get(name, 0):
                best[name] = int(count)
    if not best:
        return []

    # Per-tag local coverage uses the same LIKE-on-JSON pattern as /coverage.
    out: list[TagSuggestion] = []
    for name, instance_count in best.items():
        if name in tracked_set or instance_count < min_instance_count:
            continue
        like = f'%"{name}"%'
        local_count = int(session.exec(
            select(func.count()).select_from(Question).where(  # type: ignore[arg-type]
                col(Question.tags).like(like)
            )
        ).one() or 0)
        out.append(TagSuggestion(
            tag=name,
            instance_count=instance_count,
            local_count=local_count,
            coverage_ratio=round(local_count / max(instance_count, 1), 4),
        ))

    out.sort(key=lambda s: s.instance_count, reverse=True)
    return out[:limit]


class TrendItem(BaseModel):
    main_category: str
    sub_category: str
    recent_count: int             # last `recent_days` window
    trailing_avg_per_window: float  # avg per same-sized window over `baseline_days`
    multiplier: float              # recent / max(trailing_avg, 1)
    is_rising: bool                # True when multiplier >= threshold AND recent_count >= floor


@router.get("/trends", response_model=list[TrendItem])
async def get_trends(
    product: str = Query(..., description="Product/tag"),
    recent_days: int = Query(7, ge=1, le=90, description="Recent window in days"),
    baseline_days: int = Query(30, ge=7, le=365, description="Baseline window in days"),
    threshold: float = Query(2.0, ge=1.0, le=20.0, description="Multiplier to flag as rising"),
    min_recent: int = Query(2, ge=1, description="Minimum recent_count to flag (noise floor)"),
    session: Session = Depends(get_session),
) -> list[TrendItem]:
    """Compare per-(main,sub) volume in the last `recent_days` against the
    trailing `baseline_days` average. Flags categories whose recent volume
    is `threshold`× the trailing baseline.

    Returns every category that has at least one signal classification in
    either window, sorted with rising ones first then by multiplier.
    """
    if recent_days >= baseline_days:
        raise HTTPException(
            status_code=422,
            detail="baseline_days must be greater than recent_days.",
        )

    now = utcnow()
    recent_since = now - timedelta(days=recent_days)
    baseline_since = now - timedelta(days=baseline_days)

    # Pull every signal classification in baseline window; group by category.
    all_qs = session.exec(
        select(Question).where(Question.created_at >= baseline_since)
    ).all()
    questions = [q for q in all_qs if _question_has_tag(q, product)]
    q_by_id: dict[int, Question] = {q.id: q for q in questions if q.id is not None}
    if not q_by_id:
        return []

    cls = session.exec(
        select(Classification).where(
            Classification.question_id.in_(list(q_by_id.keys())),  # type: ignore[attr-defined]
            Classification.is_noise == False,  # noqa: E712
        )
    ).all()

    # baseline = full window, recent = last recent_days slice
    baseline_counts: dict[tuple[str, str], int] = {}
    recent_counts: dict[tuple[str, str], int] = {}
    for c in cls:
        q = q_by_id.get(c.question_id)
        if q is None:
            continue
        key = (c.main_category, c.sub_category)
        baseline_counts[key] = baseline_counts.get(key, 0) + 1
        if q.created_at >= recent_since:
            recent_counts[key] = recent_counts.get(key, 0) + 1

    # Trailing baseline excludes the recent window so the comparison is fair.
    trailing_window_days = baseline_days - recent_days
    items: list[TrendItem] = []
    for key, baseline_total in baseline_counts.items():
        recent = recent_counts.get(key, 0)
        trailing_total = baseline_total - recent
        # Express the trailing baseline as the average count for a window of
        # `recent_days` length, so the comparison is apples-to-apples.
        trailing_avg = (trailing_total / trailing_window_days) * recent_days
        denom = max(trailing_avg, 1.0)
        multiplier = recent / denom
        is_rising = recent >= min_recent and multiplier >= threshold
        items.append(TrendItem(
            main_category=key[0], sub_category=key[1],
            recent_count=recent,
            trailing_avg_per_window=round(trailing_avg, 2),
            multiplier=round(multiplier, 2),
            is_rising=is_rising,
        ))

    items.sort(key=lambda t: (not t.is_rising, -t.multiplier, -t.recent_count))
    return items


@router.get("/patterns", response_model=list[PatternItem])
async def get_patterns(
    product: str | None = Query(None, description="Filter by product/tag"),
    window: int | None = Query(None, ge=1, le=365, description="Filter by window in days"),
    session: Session = Depends(get_session),
) -> list[PatternItem]:
    """All persisted patterns, optionally filtered by product and/or window."""
    stmt = select(Pattern)
    if product is not None:
        stmt = stmt.where(Pattern.product_tag == product)
    if window is not None:
        stmt = stmt.where(Pattern.window_days == window)
    db_patterns = session.exec(stmt).all()
    return sorted(
        [
            PatternItem(
                main_category=p.main_category,
                sub_category=p.sub_category,
                question_count=p.question_count,
                distinct_users=p.distinct_users,
                suggested_action=p.suggested_action,
                first_seen=p.first_seen,
                last_seen=p.last_seen,
                summary=p.summary,
            )
            for p in db_patterns
        ],
        key=lambda pi: pi.question_count,
        reverse=True,
    )


@router.get("/questions", response_model=list[QuestionRef])
async def get_questions(
    product: str = Query(..., description="Product/tag"),
    main: str = Query(..., description="Main category"),
    sub: str | None = Query(None, description="Sub-category (omit for all of `main`)"),
    window: int = Query(30, ge=1, le=365, description="Window in days"),
    from_date: str | None = Query(None, description="YYYY-MM-DD"),
    to_date: str | None = Query(None, description="YYYY-MM-DD"),
    noise: bool = Query(False, description="Return noise/excluded questions instead of signal"),
    session: Session = Depends(get_session),
) -> list[QuestionRef]:
    """Questions behind a category, or noise questions when noise=true.

    The drill-down always hydrates each question's stored answers so the UI
    drawer can show the question alongside its answers.
    """
    return _questions_for(
        product, window, main, sub, session, from_date, to_date, noise,
        include_answers=True,
    )


class UnclassifiedReason(BaseModel):
    reason: str
    count: int


class TagMetrics(BaseModel):
    tag: str
    total_questions: int
    answered: int
    unanswered: int
    classified: int
    unclassified: int


class MetricsSummary(BaseModel):
    window_days: int
    from_date: str | None
    to_date: str | None
    tags: list[str]              # tags actually present in the range (breakdown keys)
    total_questions: int
    answered: int
    unanswered: int
    classified: int              # has >=1 Classification row (signal or noise)
    unclassified: int            # fetched but never run through Analysis — "skipped/missing"
    unclassified_reasons: list[UnclassifiedReason]
    by_tag: list[TagMetrics]


@router.get("/metrics", response_model=MetricsSummary)
async def get_metrics(
    tags: str = Query("", description="Comma-separated tags; empty = every tag present in range"),
    window: int = Query(30, ge=1, le=365, description="Window in days"),
    from_date: str | None = Query(None, description="YYYY-MM-DD — overrides window"),
    to_date: str | None = Query(None, description="YYYY-MM-DD — overrides window"),
    session: Session = Depends(get_session),
) -> MetricsSummary:
    """Pipeline-health metrics for a date range: fetched vs. answered vs. classified.

    Distinct from /summary — this reports on the *ingestion/analysis pipeline itself*
    (did we fetch everything, did Analysis run on it) rather than category insights.
    A question counts as "classified" once it has at least one Classification row
    (signal or noise); the classifier always writes a row (falling back to a noise
    classification on repeated model failures — see services/classifier.py), so an
    "unclassified" question here means Analysis simply has not been run over it yet
    for this product/window.
    """
    since, until = resolve_range(window, from_date, to_date)
    all_qs = session.exec(
        select(Question).where(Question.created_at >= since, Question.created_at <= until)
    ).all()

    wanted = [t.strip().lower() for t in tags.split(",") if t.strip()]
    questions = (
        [q for q in all_qs if any(_question_has_tag(q, t) for t in wanted)]
        if wanted
        else all_qs
    )

    q_ids = [q.id for q in questions if q.id is not None]
    classified_ids: set[int] = set()
    if q_ids:
        classified_ids = set(
            session.exec(
                select(Classification.question_id).where(
                    Classification.question_id.in_(q_ids)  # type: ignore[attr-defined]
                )
            ).all()
        )

    total = len(questions)
    answered = sum(1 for q in questions if q.answer_count > 0)
    classified = sum(1 for q in questions if q.id in classified_ids)
    unclassified = total - classified

    unclassified_reasons: list[UnclassifiedReason] = []
    if unclassified:
        unclassified_reasons.append(UnclassifiedReason(
            reason="Fetched but not yet processed by an Analysis run for this product/window",
            count=unclassified,
        ))

    tag_set = sorted(wanted) if wanted else sorted({t for q in questions for t in _safe_tags(q)})
    by_tag: list[TagMetrics] = []
    for t in tag_set:
        tqs = [q for q in questions if _question_has_tag(q, t)]
        t_total = len(tqs)
        t_answered = sum(1 for q in tqs if q.answer_count > 0)
        t_classified = sum(1 for q in tqs if q.id in classified_ids)
        by_tag.append(TagMetrics(
            tag=t,
            total_questions=t_total,
            answered=t_answered,
            unanswered=t_total - t_answered,
            classified=t_classified,
            unclassified=t_total - t_classified,
        ))
    by_tag.sort(key=lambda x: x.total_questions, reverse=True)

    log.info(
        "metrics_built", window_days=window, tags=tag_set,
        total=total, answered=answered, classified=classified, unclassified=unclassified,
    )

    return MetricsSummary(
        window_days=window,
        from_date=from_date,
        to_date=to_date,
        tags=tag_set,
        total_questions=total,
        answered=answered,
        unanswered=total - answered,
        classified=classified,
        unclassified=unclassified,
        unclassified_reasons=unclassified_reasons,
        by_tag=by_tag,
    )


@router.get("/report")
async def get_report(
    product: str = Query(..., description="Product/tag to export"),
    window: int = Query(30, ge=1, le=365, description="Window in days"),
    report_format: Literal["md", "json", "pdf"] = Query("json", alias="format"),
    from_date: str | None = Query(None, description="YYYY-MM-DD"),
    to_date: str | None = Query(None, description="YYYY-MM-DD"),
    session: Session = Depends(get_session),
) -> Response:
    """Product-Owner export — JSON, Markdown, or PDF."""
    summary = _build_summary(
        product, window, session, include_questions=True, from_date=from_date, to_date=to_date
    )
    slug = product.replace(" ", "_")
    remediations = _remediation_export(product, window, session)

    if report_format == "json":
        payload = json.loads(summary.model_dump_json())
        payload["remediations"] = remediations
        return Response(
            content=json.dumps(payload, indent=2, default=str),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="report_{slug}_{window}d.json"'
            },
        )

    if report_format == "pdf":
        # Lazy import — reportlab is heavy; only load when a PDF is asked for.
        from services.pdf_report import render_pdf  # noqa: PLC0415
        payload = json.loads(summary.model_dump_json())
        pdf_bytes = render_pdf(payload, remediations)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="report_{slug}_{window}d.pdf"'
            },
        )

    md = _render_markdown(summary)
    rem_lines = _remediation_md(remediations)
    if rem_lines:
        md += "\n".join(rem_lines) + "\n"
    return Response(
        content=md.encode(),
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="report_{slug}_{window}d.md"'
        },
    )
