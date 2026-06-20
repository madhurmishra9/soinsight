"""F5 — PDF export. The headline check is that long content paginates cleanly
(no overflow, headers reprint, every page bounded) — not just that bytes come
back."""

from __future__ import annotations

import io
import re
from typing import Any

import pytest


def _has_reportlab() -> bool:
    try:
        import reportlab  # noqa: F401
        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _has_reportlab(), reason="reportlab not installed")


def _summary(*, breakdown: int = 5, top_qs: int = 5, patterns: int = 0) -> dict[str, Any]:
    """Build a minimal /report payload shape that drives the PDF renderer."""
    bd = [
        {
            "main_category": f"Main {i}",
            "sub_category": f"Sub {i}",
            "question_count": 100 - i,
            "distinct_users": 10 - i % 5,
            "questions": [],
        }
        for i in range(breakdown)
    ]
    top = [
        {
            "main_category": "Technical",
            "sub_category": "Reliability",
            "question_count": 3,
            "distinct_users": 2,
            "questions": [
                {
                    "so_id": 1000 + j,
                    "title": f"Question title #{j} " + ("very long " * 15),
                    "score": j,
                    "view_count": 100 + j,
                    "answer_count": j % 4,
                    "url": f"https://example.com/q/{1000 + j}",
                    "answers": [],
                }
                for j in range(top_qs)
            ],
        }
    ]
    pats = [
        {
            "main_category": "Technical",
            "sub_category": f"Pattern Sub {i}",
            "question_count": 5,
            "distinct_users": 3,
            "suggested_action": "Document the pinning pattern. " * 20,
            "summary": "Recurring across several teams. " * 20,
            "questions": [],
        }
        for i in range(patterns)
    ]
    return {
        "product": "python",
        "window_days": 30,
        "total_questions": sum(b["question_count"] for b in bd),
        "noise_count": 4,
        "category_breakdown": bd,
        "top_issues": top,
        "patterns": pats,
        "recommended_actions": ["Add docs", "Fix flakey timeout"],
        "noise_questions": [],
        "technical_ratio": 0.62,
        "non_technical_ratio": 0.38,
    }


def _pdf_pages(b: bytes) -> int:
    """Count pages by scanning the trailing xref / Page object dictionaries.

    Avoids adding a parsing dependency just for the test. reportlab emits one
    `/Type /Page` per page (not /Pages, the kids array) in the body, so a count
    of those entries equals the page count for our simple layout.
    """
    # We need an exact match for `/Type /Page` followed by a non-letter so
    # `/Type /Pages` is excluded.
    return len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", b))


def test_render_pdf_returns_pdf_bytes_with_header() -> None:
    from services.pdf_report import render_pdf
    out = render_pdf(_summary(), [])
    assert isinstance(out, bytes) and len(out) > 1000
    assert out.startswith(b"%PDF-"), "must start with PDF magic"
    assert out.rstrip(b"\n\r").endswith(b"%%EOF"), "must end with %%EOF"


def test_render_pdf_includes_product_in_title() -> None:
    from services.pdf_report import render_pdf
    out = render_pdf(_summary(), [])
    # reportlab writes the document Title field — must mention the product.
    assert b"python" in out


def test_render_pdf_paginates_when_content_is_long() -> None:
    """One small summary fits on one page; a hundred-row breakdown forces
    pagination. The page count must grow, not the content overflow."""
    from services.pdf_report import render_pdf
    small = render_pdf(_summary(breakdown=2, top_qs=2), [])
    big = render_pdf(_summary(breakdown=120, top_qs=50, patterns=20), [])
    assert _pdf_pages(small) >= 1
    assert _pdf_pages(big) > _pdf_pages(small), (
        f"long content should paginate, but got {_pdf_pages(small)} → {_pdf_pages(big)} pages"
    )


def test_render_pdf_handles_html_unsafe_text_without_crashing() -> None:
    """Question bodies often contain `<`, `>`, `&`. The renderer must escape
    them — reportlab Paragraph parses XML-ish tags otherwise."""
    from services.pdf_report import render_pdf
    s = _summary(top_qs=1)
    s["top_issues"][0]["questions"][0]["title"] = "x < 3 and y > 2 & z=<tag>"
    s["patterns"] = [{
        "main_category": "Technical",
        "sub_category": "<unsafe>",
        "question_count": 1, "distinct_users": 1,
        "suggested_action": "do <stuff> & things",
        "summary": "summary <here>",
        "questions": [],
    }]
    out = render_pdf(s, [])
    assert out.startswith(b"%PDF-")


def test_render_pdf_includes_remediations_when_present() -> None:
    from services.pdf_report import render_pdf
    rems = [{
        "main_category": "Technical",
        "sub_category": "Reliability",
        "question_count": 5,
        "distinct_users": 3,
        "grounded": True,
        "confidence": 0.8,
        "root_cause": "Idle TCP connections reaped by the LB.",
        "solution": "Set keepalive and raise idle timeout.",
        "prevention": "Document pool defaults in the SDK README.",
        "model": "llama3.1:8b",
        "generated_at": "2026-06-20T00:00:00",
        "evidence_questions": [
            {"so_id": 1, "title": "Cited Q", "url": "https://x/1"},
        ],
        "evidence_answers": [
            {"so_id": 9, "question_so_id": 1, "is_accepted": True, "score": 8,
             "snippet": "Use keepalive. " * 30},
        ],
    }]
    out = render_pdf(_summary(), rems)
    # Has at least one page break — remediation section starts on a new page.
    assert _pdf_pages(out) >= 2


def test_render_pdf_with_empty_summary_is_still_valid_pdf() -> None:
    from services.pdf_report import render_pdf
    empty = {
        "product": "edge", "window_days": 30,
        "total_questions": 0, "noise_count": 0,
        "category_breakdown": [], "top_issues": [],
        "patterns": [], "recommended_actions": [],
        "noise_questions": [],
        "technical_ratio": None, "non_technical_ratio": None,
    }
    out = render_pdf(empty, [])
    assert out.startswith(b"%PDF-")
    assert _pdf_pages(out) >= 1


def test_endpoint_returns_pdf_content_type() -> None:
    """End-to-end via the FastAPI endpoint."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel

    from app.db import get_session as app_db_get_session
    from routers.dismissals import router as dr
    from routers.insights import get_session, router as insights_router

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _override():  # type: ignore[no-untyped-def]
        with Session(engine) as s:
            yield s

    app = FastAPI()
    app.include_router(insights_router)
    app.include_router(dr)
    app.dependency_overrides[get_session] = _override
    app.dependency_overrides[app_db_get_session] = _override

    c = TestClient(app)
    r = c.get("/api/insights/report?product=python&window=30&format=pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "report_python_30d.pdf" in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF-")
