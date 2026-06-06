"""
Classification eval harness for SOInsight.

Loads a hand-labelled CSV, runs the classifier against real Ollama,
and produces per-category precision/recall/F1 + a confusion matrix
written to a Markdown report.

Usage (from backend/ directory):
    python -m eval
    python -m eval --csv eval/eval_data.csv --ollama http://localhost:11434 --out eval_report.md
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from app.models import Question
from app.taxonomy import TAXONOMY
from services.classifier import ClassificationResult, ClassifierService

log = structlog.get_logger("soinsight.eval")

MAIN_CATEGORIES = list(TAXONOMY.keys())

# ─── CSV loading ──────────────────────────────────────────────────────────────


def load_csv(path: Path) -> list[dict[str, str]]:
    """Load hand-labelled eval data. Required columns: title, body, expected_main, expected_sub."""
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    required = ("title", "body", "expected_main", "expected_sub")
    missing = [c for c in required if c not in (rows[0] if rows else {})]
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}")
    return rows


# ─── DB seeding ───────────────────────────────────────────────────────────────


def _make_engine() -> Any:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_questions(engine: Any, rows: list[dict[str, str]]) -> list[Question]:
    """Insert each CSV row as a Question and return the refreshed ORM objects."""
    questions: list[Question] = []
    with Session(engine) as session:
        for i, row in enumerate(rows, start=1):
            q = Question(
                so_id=i,
                title=row["title"],
                body=row.get("body", ""),
                tags=json.dumps([]),
                score=0,
                view_count=0,
                created_at=datetime.utcnow(),
                author_id=1,
                answer_count=0,
                has_accepted=False,
            )
            session.add(q)
            session.commit()
            session.refresh(q)
            questions.append(q)
    return questions


# ─── Metrics computation ──────────────────────────────────────────────────────


def compute_metrics(
    results: list[ClassificationResult],
    expected: list[dict[str, str]],
) -> dict[str, Any]:
    """
    Compute per-main-category precision/recall/F1 and a confusion matrix.

    results[i] corresponds to expected[i] — both lists must be the same length
    and in the same order as the seeded questions.
    """
    pairs: list[tuple[str, str]] = [
        (r.main_category, e["expected_main"])
        for r, e in zip(results, expected, strict=False)
    ]

    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)

    for pred, exp in pairs:
        if pred == exp:
            tp[pred] += 1
        else:
            fp[pred] += 1
            fn[exp] += 1

    per_category: dict[str, dict[str, Any]] = {}
    for cat in MAIN_CATEGORIES:
        p_denom = tp[cat] + fp[cat]
        r_denom = tp[cat] + fn[cat]
        prec = tp[cat] / p_denom if p_denom else 0.0
        rec = tp[cat] / r_denom if r_denom else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        per_category[cat] = {
            "tp": tp[cat],
            "fp": fp[cat],
            "fn": fn[cat],
            "precision": round(prec, 3),
            "recall": round(rec, 3),
            "f1": round(f1, 3),
            "support": tp[cat] + fn[cat],
        }

    total = len(pairs)
    correct = sum(1 for pred, exp in pairs if pred == exp)
    accuracy = round(correct / total, 3) if total else 0.0

    # Confusion matrix: rows = expected label, cols = predicted label
    confusion: dict[str, dict[str, int]] = {c: defaultdict(int) for c in MAIN_CATEGORIES}
    for pred, exp in pairs:
        if exp in confusion:
            confusion[exp][pred] += 1

    return {
        "accuracy": accuracy,
        "total": total,
        "correct": correct,
        "per_category": per_category,
        "confusion": {k: dict(v) for k, v in confusion.items()},
    }


# ─── Report rendering ─────────────────────────────────────────────────────────


def render_report(metrics: dict[str, Any], csv_path: Path) -> str:
    lines: list[str] = []
    lines.append("# SOInsight Classifier Eval Report\n")
    lines.append(f"- Dataset: `{csv_path.name}` ({metrics['total']} questions)")
    lines.append(
        f"- Overall accuracy: **{metrics['accuracy']:.1%}** "
        f"({metrics['correct']}/{metrics['total']} correct)\n"
    )

    lines.append("## Per-Category Metrics\n")
    lines.append("| Category | Precision | Recall | F1 | Support |")
    lines.append("|---|---|---|---|---|")
    for cat, m in metrics["per_category"].items():
        weak = " ⚠" if m["support"] > 0 and m["f1"] < 0.7 else ""
        lines.append(
            f"| {cat}{weak} | {m['precision']:.3f} | {m['recall']:.3f}"
            f" | {m['f1']:.3f} | {m['support']} |"
        )
    lines.append("")

    lines.append("## Confusion Matrix\n")
    lines.append("_Rows = expected label · Columns = predicted label_\n")
    cats = MAIN_CATEGORIES
    short = [c.split(" /")[0][:14] for c in cats]  # truncate for readability
    lines.append("| Expected \\ Predicted | " + " | ".join(short) + " |")
    lines.append("|---|" + "---|" * len(cats))
    for exp_cat, short_name in zip(cats, short, strict=True):
        vals = [
            str(metrics["confusion"].get(exp_cat, {}).get(pred, 0))
            for pred in cats
        ]
        lines.append(f"| {short_name} | " + " | ".join(vals) + " |")
    lines.append("")

    lines.append("## Weak Categories (F1 < 0.70)\n")
    weak = [
        (cat, m)
        for cat, m in metrics["per_category"].items()
        if m["support"] > 0 and m["f1"] < 0.7
    ]
    if not weak:
        lines.append("All categories with sufficient support achieved F1 ≥ 0.70. ✓\n")
    else:
        for cat, m in sorted(weak, key=lambda x: x[1]["f1"]):
            lines.append(f"### {cat} (F1 = {m['f1']:.3f})")
            lines.append(_improvement_advice(cat, m))
            lines.append("")

    lines.append("## How to Improve Classifier Quality\n")
    lines.append(
        "1. **Add more few-shot examples** for weak categories in `services/classifier.py` "
        "(`_FEW_SHOT` list). Aim for 3–4 examples per sub-category.\n"
        "2. **Use a larger local model** — upgrade Ollama to `llama3.1:70b` for better "
        "zero-shot generalisation on ambiguous questions.\n"
        "3. **Refine prompt boundaries** — the most-confused pairs (visible in the matrix) "
        "benefit most from explicit contrastive examples, e.g. Awareness vs Documentation.\n"
        "4. **Expand the eval dataset** — add more questions per category, especially for "
        "categories with low support in this run.\n"
        "5. **Re-run eval after changes** — `python -m eval` to track F1 improvements.\n"
    )

    lines.append("## Methodology\n")
    lines.append(
        "- Classifier: Ollama `llama3.1:8b` with 2 few-shot examples per main category.\n"
        "- Metrics: per-category precision/recall/F1 (one-vs-rest, main category only).\n"
        "- Confusion matrix: rows = ground-truth label, columns = predicted label.\n"
        "- Questions seeded into a fresh in-memory SQLite DB for each eval run.\n"
        "- Sub-category accuracy is not separately tracked — extend `compute_metrics` if needed.\n"
    )

    return "\n".join(lines) + "\n"


def _improvement_advice(cat: str, m: dict[str, Any]) -> str:
    advice: dict[str, str] = {
        "Product": (
            "- Differentiate Feature Gap (missing capability) vs Demand Signal (nice-to-have).\n"
            "- Add Integration Gap examples — commonly confused with Operational."
        ),
        "Documentation": (
            "- Add examples contrasting Missing Documentation (no page exists) vs "
            "Unclear (page exists but confusing).\n"
            "- Conflicting Information often looks like Awareness — add contrastive pairs."
        ),
        "Operational": (
            "- Separate Configuration Complexity (too many knobs) from Setup issues.\n"
            "- Distinguish from Technical (infra/config pain vs code/runtime failure)."
        ),
        "Awareness": (
            "- Awareness ('didn't know it existed') is easily confused with Documentation "
            "('can't find the docs').\n"
            "- Add explicit contrastive few-shot: 'found by accident' = Awareness; "
            "'docs missing' = Documentation."
        ),
        "Technical": (
            "- Reliability (crashes/OOM) vs Performance (slow) — add timed-failure examples.\n"
            "- Poor error handling co-occurs with Reliability — the primary pain point wins."
        ),
        "Security / Compliance": (
            "- Compliance questions look like Documentation — add regulatory-mandate framing.\n"
            "- Access control confusion vs Network issues: add examples of each clearly."
        ),
        "Adoption / Migration": (
            "- Breaking changes vs Migration challenges: former is reactive, latter proactive.\n"
            "- Difficulty getting started often overlaps with Operational setup issues."
        ),
        "Misuse / Noise": (
            "- Terse questions may be noise but some one-liners are valid — low recall expected.\n"
            "- Consider a confidence threshold: low-confidence predictions → route to noise."
        ),
    }
    base = advice.get(cat, "- Add more representative few-shot examples for this category.")
    if m["fp"] > m["fn"]:
        extra = f"\n- **Over-predicted** ({m['fp']} FP): classifier assigns this label too readily."
    elif m["fn"] > m["fp"]:
        extra = (
            f"\n- **Under-predicted** ({m['fn']} FN): "
            "classifier misses questions that belong here."
        )
    else:
        extra = ""
    return base + extra


# ─── Main async runner ────────────────────────────────────────────────────────


async def run_eval(
    csv_path: Path,
    ollama_url: str,
    output_path: Path,
    transport: Any = None,  # injectable mock transport for tests
) -> int:
    """Run the full eval pipeline. Returns exit code (0 = success, 1 = error)."""
    log.info("eval_start", csv=str(csv_path), output=str(output_path))

    try:
        rows = load_csv(csv_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: Cannot load CSV: {exc}", file=sys.stderr)
        return 1

    if not rows:
        print("ERROR: CSV is empty.", file=sys.stderr)
        return 1

    engine = _make_engine()
    questions = _seed_questions(engine, rows)
    expected = [
        {"expected_main": r["expected_main"], "expected_sub": r["expected_sub"]}
        for r in rows
    ]

    print(f"Loaded {len(questions)} questions. Running classifier…")
    print("(This may take several minutes — each question is batched through Ollama.)\n")

    try:
        svc = ClassifierService(ollama_url=ollama_url, transport=transport)
        results = await svc.classify_questions(questions, engine)
    except Exception as exc:
        print(f"ERROR: Classifier failed: {exc}", file=sys.stderr)
        print("Is Ollama running? Start it with: ollama serve", file=sys.stderr)
        return 1

    if not results:
        print("ERROR: Classifier returned no results.", file=sys.stderr)
        return 1

    metrics = compute_metrics(results, expected)
    report = render_report(metrics, csv_path)

    output_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"Report written to: {output_path}\n")
    acc = metrics["accuracy"]
    print(f"Overall accuracy: {acc:.1%} ({metrics['correct']}/{metrics['total']})\n")

    for cat, m in metrics["per_category"].items():
        if m["support"] > 0:
            flag = " ⚠" if m["f1"] < 0.7 else ""
            print(
                f"  {cat:<30}  F1={m['f1']:.3f}  "
                f"P={m['precision']:.3f}  R={m['recall']:.3f}  "
                f"support={m['support']}{flag}"
            )

    log.info("eval_done", accuracy=metrics["accuracy"], total=metrics["total"])
    return 0
