"""
Insights API — pure reads from SQLite.

All endpoints query the local DB only; no SO or Ollama calls.
technical_ratio is APPROXIMATE (question-tag heuristic, not a verified user attribute).
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timedelta
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.db import engine as app_engine
from app.models import Classification, Pattern, Question
from app.settings import settings as app_settings
from routers.settings import _current_config
from services.aggregator import _compute_technical_ratio, _question_has_tag

log = structlog.get_logger("soinsight.routers.insights")

router = APIRouter(prefix="/api/insights", tags=["insights"])

_NOISE_MAIN = "Misuse / Noise"


# ─── Session dependency ────────────────────────────────────────────────────────

def get_session() -> Generator[Session, None, None]:
    with Session(app_engine) as session:
        yield session


# ─── Response models ───────────────────────────────────────────────────────────

class QuestionRef(BaseModel):
    so_id: int
    title: str
    score: int
    view_count: int
    created_at: datetime
    url: str | None = None


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


def _questions_by_category(
    product: str, window_days: int, session: Session,
) -> dict[tuple[str, str], list[QuestionRef]]:
    """Group every signal (non-noise) question for a product/window by (main, sub)."""
    since = datetime.utcnow() - timedelta(days=window_days)
    all_qs = session.exec(select(Question).where(Question.created_at >= since)).all()
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
            QuestionRef(
                so_id=q.so_id,
                title=q.title,
                score=q.score,
                view_count=q.view_count,
                created_at=q.created_at,
                url=_question_url(q.so_id),
            )
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
) -> list[QuestionRef]:
    """Questions behind a category — all sub-categories of `main` if `sub` is omitted."""
    grouped = _questions_by_category(product, window_days, session)
    if sub:
        return grouped.get((main, sub), [])

    out: list[QuestionRef] = []
    for (m, _s), qs in grouped.items():
        if m == main:
            out.extend(qs)
    return sorted(out, key=lambda x: x.score, reverse=True)


# ─── Core summary builder (shared by /summary and /report) ────────────────────

def _build_summary(
    product: str, window_days: int, session: Session, include_questions: bool = False,
) -> InsightsSummary:
    since = datetime.utcnow() - timedelta(days=window_days)

    all_qs = session.exec(select(Question).where(Question.created_at >= since)).all()
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

    grouped = _questions_by_category(product, window_days, session) if include_questions else {}

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
        category_breakdown=breakdown,
        top_issues=breakdown[:5],
        patterns=pattern_items,
        recommended_actions=recommended_actions,
        technical_ratio=tech_ratio,
        non_technical_ratio=non_tech_ratio,
    )


# ─── Markdown renderer ─────────────────────────────────────────────────────────

def _q_lines(questions: list[QuestionRef], indent: str = "   ") -> list[str]:
    """Render question references as linked (or titled) bullet lines."""
    out: list[str] = []
    for q in questions:
        link = f"[{q.title}]({q.url})" if q.url else f"{q.title} (#{q.so_id})"
        out.append(f"{indent}- {link} — score {q.score}, {q.view_count} views")
    return out


def _render_markdown(s: InsightsSummary) -> str:
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
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


# ─── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/summary", response_model=InsightsSummary)
async def get_summary(
    product: str = Query(..., description="Product/tag to summarise"),
    window: int = Query(30, ge=1, le=365, description="Window in days"),
    session: Session = Depends(get_session),
) -> InsightsSummary:
    """One summary per product/tag: category breakdown, patterns, recommendations."""
    return _build_summary(product, window, session)


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
    session: Session = Depends(get_session),
) -> list[QuestionRef]:
    """Questions behind a category — drives every drill-down surface on the dashboard."""
    return _questions_for(product, window, main, sub, session)


@router.get("/report")
async def get_report(
    product: str = Query(..., description="Product/tag to export"),
    window: int = Query(30, ge=1, le=365, description="Window in days"),
    report_format: Literal["md", "json"] = Query("json", alias="format"),
    session: Session = Depends(get_session),
) -> Response:
    """Product-Owner export — JSON or Markdown."""
    summary = _build_summary(product, window, session, include_questions=True)
    slug = product.replace(" ", "_")

    if report_format == "json":
        return Response(
            content=summary.model_dump_json(indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="report_{slug}_{window}d.json"'
            },
        )

    md = _render_markdown(summary)
    return Response(
        content=md.encode(),
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="report_{slug}_{window}d.md"'
        },
    )
