"""
PDF report renderer for SOInsight summaries (F5).

Uses reportlab's Platypus flowables so long question lists, wide tables, and
multi-paragraph remediation prose **spill cleanly across pages** — the headline
acceptance criterion. Tables use repeatRows=1 so headers reprint on every page;
KeepTogether keeps section titles attached to their first row of content.
"""

from __future__ import annotations

import html
from datetime import datetime
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ── Styles ────────────────────────────────────────────────────────────────────

_BASE = getSampleStyleSheet()
_H1 = ParagraphStyle("H1", parent=_BASE["Title"], fontSize=20, leading=24, spaceAfter=8)
_H2 = ParagraphStyle("H2", parent=_BASE["Heading2"], fontSize=14, leading=18, spaceBefore=12, spaceAfter=6)
_H3 = ParagraphStyle("H3", parent=_BASE["Heading3"], fontSize=12, leading=15, spaceBefore=8, spaceAfter=4)
_BODY = ParagraphStyle("Body", parent=_BASE["BodyText"], fontSize=10, leading=13, spaceAfter=4, wordWrap="CJK")
_MUTED = ParagraphStyle(
    "Muted", parent=_BODY, textColor=colors.HexColor("#6b7280"), fontSize=9, leading=12,
)
_CELL = ParagraphStyle("Cell", parent=_BODY, fontSize=9, leading=11, spaceAfter=0)

_TABLE_STYLE = TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#111827")),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE", (0, 0), (-1, -1), 9),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ("TOPPADDING", (0, 0), (-1, -1), 3),
    ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
    ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.HexColor("#e5e7eb")),
    ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
])

# ── Helpers ───────────────────────────────────────────────────────────────────


def _esc(text: str | None) -> str:
    """Escape user-supplied strings so paragraphs treat them as plain text.

    Reportlab parses XML-ish tags inside Paragraph; an unescaped `<` from a body
    of code-heavy question text will raise ParaError. html.escape covers `<`,
    `>`, `&`, and the quote variants.
    """
    return html.escape((text or "").strip(), quote=True)


def _para(text: str | None, style: ParagraphStyle = _BODY) -> Paragraph:
    return Paragraph(_esc(text), style)


def _cell(text: str | None) -> Paragraph:
    """Cell value as a Paragraph so reportlab wraps long titles across lines
    instead of overflowing the column."""
    return Paragraph(_esc(text), _CELL)


def _link_cell(title: str, url: str | None) -> Paragraph:
    title_e = _esc(title)
    if not url:
        return Paragraph(title_e, _CELL)
    # `<a href=…>` is the only XML tag we deliberately let through.
    url_e = _esc(url)
    return Paragraph(f'<a href="{url_e}" color="#1d4ed8">{title_e}</a>', _CELL)


# ── Page footer ───────────────────────────────────────────────────────────────


def _add_footer(canvas, doc) -> None:  # type: ignore[no-untyped-def]
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#6b7280"))
    page = canvas.getPageNumber()
    canvas.drawString(0.6 * inch, 0.4 * inch, "SOInsight")
    canvas.drawRightString(LETTER[0] - 0.6 * inch, 0.4 * inch, f"Page {page}")
    canvas.restoreState()


# ── Sections ──────────────────────────────────────────────────────────────────


def _summary_table(s: dict[str, Any]) -> Table:
    rows = [
        [_cell("Metric"), _cell("Value")],
        [_cell("Total signal questions"), _cell(str(s.get("total_questions", 0)))],
        [_cell("Noise volume"), _cell(str(s.get("noise_count", 0)))],
        [_cell("Patterns detected"), _cell(str(len(s.get("patterns", []))))],
    ]
    if s.get("technical_ratio") is not None:
        tech = round(s["technical_ratio"] * 100, 1)
        non = round((s.get("non_technical_ratio") or 0.0) * 100, 1)
        rows.append([
            _cell("Technical / Non-technical (APPROXIMATE)"),
            _cell(f"{tech}% / {non}%"),
        ])
    table = Table(rows, colWidths=[3.0 * inch, 3.5 * inch], repeatRows=1)
    table.setStyle(_TABLE_STYLE)
    return table


def _breakdown_table(items: list[dict[str, Any]]) -> Table:
    rows: list[list[Any]] = [[
        _cell("Main category"), _cell("Sub-category"),
        _cell("Questions"), _cell("Distinct users"),
    ]]
    for it in items:
        rows.append([
            _cell(it.get("main_category", "")),
            _cell(it.get("sub_category", "")),
            _cell(str(it.get("question_count", 0))),
            _cell(str(it.get("distinct_users", 0))),
        ])
    table = Table(
        rows,
        colWidths=[1.8 * inch, 2.4 * inch, 1.0 * inch, 1.1 * inch],
        repeatRows=1,   # header re-prints on page breaks
    )
    table.setStyle(_TABLE_STYLE)
    return table


def _questions_table(questions: list[dict[str, Any]]) -> Table:
    rows: list[list[Any]] = [[_cell("Title"), _cell("Score"), _cell("Views"), _cell("Answers")]]
    for q in questions:
        rows.append([
            _link_cell(q.get("title", ""), q.get("url")),
            _cell(str(q.get("score", 0))),
            _cell(str(q.get("view_count", 0))),
            _cell(str(q.get("answer_count", 0))),
        ])
    table = Table(
        rows,
        colWidths=[4.5 * inch, 0.6 * inch, 0.7 * inch, 0.7 * inch],
        repeatRows=1,
    )
    table.setStyle(_TABLE_STYLE)
    return table


def _build_story(summary: dict[str, Any], remediations: list[dict[str, Any]]) -> list[Any]:
    story: list[Any] = []
    product = summary.get("product", "(unknown)")
    window = summary.get("window_days", 0)
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    story.append(_para(f"SOInsight Report — {product} ({window}-day window)", _H1))
    story.append(_para(f"Generated: {now_str}", _MUTED))
    story.append(Spacer(1, 6))

    # Summary table
    story.append(KeepTogether([_para("Summary", _H2), _summary_table(summary), Spacer(1, 4)]))

    # Category breakdown
    breakdown = summary.get("category_breakdown", [])
    if breakdown:
        story.append(_para("Category Breakdown", _H2))
        story.append(_breakdown_table(breakdown))
        story.append(Spacer(1, 6))

    # Top issues
    top_issues = summary.get("top_issues", [])
    if top_issues:
        story.append(_para("Top Issues", _H2))
        for i, it in enumerate(top_issues, 1):
            heading = (
                f"{i}. {it.get('main_category', '')} / {it.get('sub_category', '')}"
                f" — {it.get('question_count', 0)} questions"
                f" · {it.get('distinct_users', 0)} users"
            )
            story.append(_para(heading, _H3))
            qs = it.get("questions", [])
            if qs:
                story.append(_questions_table(qs))
            story.append(Spacer(1, 4))

    # Patterns
    patterns = summary.get("patterns", [])
    if patterns:
        story.append(PageBreak())
        story.append(_para("Key Patterns", _H2))
        for p in patterns:
            block: list[Any] = [
                _para(f"{p.get('main_category', '')}: {p.get('sub_category', '')}", _H3),
                _para(
                    f"Questions: {p.get('question_count', 0)} · "
                    f"Distinct users: {p.get('distinct_users', 0)}",
                    _MUTED,
                ),
            ]
            if p.get("suggested_action"):
                block.append(_para(f"<b>Recommended action:</b> {_esc(p['suggested_action'])}"))
            if p.get("summary"):
                block.append(_para(f"<b>Summary:</b> {_esc(p['summary'])}"))
            story.append(KeepTogether(block))
            qs = p.get("questions", [])
            if qs:
                story.append(_questions_table(qs))
            story.append(Spacer(1, 6))

    # Remediations — long prose; let Platypus paginate naturally.
    if remediations:
        story.append(PageBreak())
        story.append(_para("Remediation Guide (grounded fixes)", _H2))
        story.append(_para(
            "Source-grounded fixes for clusters of similar questions, so the same "
            "questions stop recurring. Every claim is tied to the cited sources below.",
            _MUTED,
        ))
        for r in remediations:
            story.append(_para(
                f"{r.get('main_category', '')}: {r.get('sub_category', '')}", _H3,
            ))
            flag = (
                f"grounded · {round((r.get('confidence') or 0.0) * 100)}% confidence"
                if r.get("grounded") else "NOT grounded"
            )
            story.append(_para(
                f"{r.get('question_count', 0)} questions · "
                f"{r.get('distinct_users', 0)} users · {flag}",
                _MUTED,
            ))
            if not r.get("grounded"):
                story.append(_para(r.get("prevention") or ""))
                story.append(Spacer(1, 4))
                continue
            if r.get("root_cause"):
                story.append(_para(f"<b>Root cause:</b> {_esc(r['root_cause'])}"))
            if r.get("solution"):
                story.append(_para(f"<b>Solution:</b> {_esc(r['solution'])}"))
            if r.get("prevention"):
                story.append(_para(f"<b>Prevent recurrence:</b> {_esc(r['prevention'])}"))
            ev_q = r.get("evidence_questions") or []
            if ev_q:
                story.append(_para("<b>Grounded in:</b>"))
                for q in ev_q:
                    story.append(_para(
                        f"• <a href='{_esc(q.get('url') or '')}' color='#1d4ed8'>"
                        f"{_esc(q.get('title') or '')}</a>",
                    ))
            ev_a = r.get("evidence_answers") or []
            for a in ev_a:
                tag = "accepted" if a.get("is_accepted") else f"score {a.get('score', 0)}"
                story.append(_para(
                    f"  answer to #{a.get('question_so_id', 0)} ({tag}): "
                    f"{_esc(a.get('snippet') or '')}",
                    _MUTED,
                ))
            story.append(Spacer(1, 6))

    if not story:
        story.append(_para("No data for this report.", _MUTED))
    return story


def render_pdf(summary: dict[str, Any], remediations: list[dict[str, Any]]) -> bytes:
    """Render the report dict (from /api/insights/report) as a PDF byte string."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.7 * inch,   # leaves room for the footer
        title=f"SOInsight Report — {summary.get('product', '')}",
        author="SOInsight",
    )
    doc.build(_build_story(summary, remediations), onFirstPage=_add_footer, onLaterPages=_add_footer)
    return buffer.getvalue()
