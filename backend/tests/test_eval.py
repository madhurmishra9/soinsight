"""
Tests for the eval harness (eval/run_eval.py).

Tests cover the pure-function parts of the harness:
  - CSV loading and validation
  - Metrics computation (precision/recall/F1, confusion matrix)
  - Markdown report rendering

No Ollama or DB calls are made here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eval.run_eval import (
    MAIN_CATEGORIES,
    compute_metrics,
    load_csv,
    render_report,
)
from services.classifier import ClassificationResult

# ---------------------------------------------------------------------------
# Fake ClassificationResult (only main_category used in compute_metrics)
# ---------------------------------------------------------------------------


def _result(main: str) -> ClassificationResult:
    return ClassificationResult(
        question_id=0,
        main_category=main,
        sub_category="",
        confidence=0.9,
        is_noise=False,
    )


def _expected(main: str) -> dict[str, str]:
    return {"expected_main": main, "expected_sub": ""}


# ---------------------------------------------------------------------------
# load_csv
# ---------------------------------------------------------------------------


def test_load_csv_reads_all_rows(tmp_path: Path) -> None:
    csv_file = tmp_path / "eval.csv"
    content = (
        "title,body,expected_main,expected_sub\n"
        "Q1,B1,Technical,Reliability issues or instability\n"
        "Q2,B2,Documentation,Missing Documentation\n"
    )
    csv_file.write_text(content, encoding="utf-8")

    rows = load_csv(csv_file)
    assert len(rows) == 2
    assert rows[0]["title"] == "Q1"
    assert rows[0]["expected_main"] == "Technical"
    assert rows[1]["expected_sub"] == "Missing Documentation"


def test_load_csv_raises_on_missing_column(tmp_path: Path) -> None:
    csv_file = tmp_path / "bad.csv"
    csv_file.write_text("title,body\nQ1,B1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required columns"):
        load_csv(csv_file)


def test_load_csv_raises_on_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        load_csv(Path("/nonexistent/path/eval.csv"))


def test_load_eval_data_csv_is_valid() -> None:
    """The bundled eval_data.csv must load without errors and have 50 rows."""
    data_path = Path(__file__).parent.parent / "eval" / "eval_data.csv"
    rows = load_csv(data_path)
    assert len(rows) == 50
    for row in rows:
        assert row["title"]
        assert row["expected_main"] in MAIN_CATEGORIES, (
            f"Row '{row['title']}' has unknown expected_main: {row['expected_main']}"
        )


# ---------------------------------------------------------------------------
# compute_metrics
# ---------------------------------------------------------------------------


def test_compute_metrics_perfect_accuracy() -> None:
    results = [_result("Technical"), _result("Documentation")]
    expected = [_expected("Technical"), _expected("Documentation")]
    m = compute_metrics(results, expected)

    assert m["total"] == 2
    assert m["correct"] == 2
    assert m["accuracy"] == 1.0
    assert m["per_category"]["Technical"]["tp"] == 1
    assert m["per_category"]["Technical"]["fp"] == 0
    assert m["per_category"]["Technical"]["fn"] == 0
    assert m["per_category"]["Technical"]["precision"] == 1.0
    assert m["per_category"]["Technical"]["recall"] == 1.0
    assert m["per_category"]["Technical"]["f1"] == 1.0


def test_compute_metrics_all_wrong() -> None:
    results = [_result("Technical"), _result("Technical")]
    expected = [_expected("Documentation"), _expected("Documentation")]
    m = compute_metrics(results, expected)

    assert m["correct"] == 0
    assert m["accuracy"] == 0.0
    assert m["per_category"]["Technical"]["fp"] == 2
    assert m["per_category"]["Documentation"]["fn"] == 2
    assert m["per_category"]["Technical"]["precision"] == 0.0
    assert m["per_category"]["Documentation"]["recall"] == 0.0


def test_compute_metrics_mixed() -> None:
    results = [
        _result("Technical"),
        _result("Technical"),
        _result("Documentation"),
    ]
    expected = [
        _expected("Technical"),
        _expected("Technical"),
        _expected("Product"),   # wrong: predicted Documentation, expected Product
    ]
    m = compute_metrics(results, expected)

    assert m["total"] == 3
    assert m["correct"] == 2
    assert round(m["accuracy"], 3) == round(2 / 3, 3)
    assert m["per_category"]["Technical"]["tp"] == 2
    assert m["per_category"]["Technical"]["fn"] == 0
    assert m["per_category"]["Documentation"]["fp"] == 1
    assert m["per_category"]["Product"]["fn"] == 1


def test_compute_metrics_f1_harmonic_mean() -> None:
    """
    With 1 TP, 1 FP, 1 FN for a category:
    precision = 0.5, recall = 0.5, F1 = 0.5.
    """
    results = [_result("Technical"), _result("Technical"), _result("Documentation")]
    expected = [_expected("Technical"), _expected("Documentation"), _expected("Technical")]
    m = compute_metrics(results, expected)

    cat = m["per_category"]["Technical"]
    assert cat["tp"] == 1
    assert cat["fp"] == 1
    assert cat["fn"] == 1
    assert cat["precision"] == 0.5
    assert cat["recall"] == 0.5
    assert cat["f1"] == 0.5


def test_compute_metrics_zero_support_category_has_zero_metrics() -> None:
    results = [_result("Technical")]
    expected = [_expected("Technical")]
    m = compute_metrics(results, expected)

    # Documentation has 0 support — no division-by-zero; returns zeros
    cat = m["per_category"]["Documentation"]
    assert cat["support"] == 0
    assert cat["precision"] == 0.0
    assert cat["recall"] == 0.0
    assert cat["f1"] == 0.0


def test_compute_metrics_confusion_matrix_shape() -> None:
    results = [_result("Technical"), _result("Documentation")]
    expected = [_expected("Technical"), _expected("Technical")]
    m = compute_metrics(results, expected)

    # Technical predicted where Technical expected → TP in confusion matrix
    assert m["confusion"]["Technical"]["Technical"] == 1
    # Documentation predicted where Technical expected → FP for Documentation, FN for Technical
    assert m["confusion"]["Technical"]["Documentation"] == 1


def test_compute_metrics_all_categories_present_in_per_category() -> None:
    results = [_result("Technical")]
    expected = [_expected("Technical")]
    m = compute_metrics(results, expected)
    for cat in MAIN_CATEGORIES:
        assert cat in m["per_category"]


# ---------------------------------------------------------------------------
# render_report
# ---------------------------------------------------------------------------


def test_render_report_contains_all_sections(tmp_path: Path) -> None:
    results = [_result("Technical"), _result("Technical"), _result("Technical")]
    expected = [_expected("Technical")] * 3
    m = compute_metrics(results, expected)
    report = render_report(m, Path("eval_data.csv"))

    assert "# SOInsight Classifier Eval Report" in report
    assert "Per-Category Metrics" in report
    assert "Confusion Matrix" in report
    assert "How to Improve" in report
    assert "Methodology" in report


def test_render_report_flags_weak_categories() -> None:
    # Make Technical have F1=0 (all wrong)
    results = [_result("Documentation"), _result("Documentation"), _result("Documentation")]
    expected = [_expected("Technical")] * 3
    m = compute_metrics(results, expected)
    report = render_report(m, Path("test.csv"))

    # Technical has F1=0, below 0.70 threshold — should appear in Weak section
    assert "Technical" in report


def test_render_report_no_weak_when_all_perfect() -> None:
    results = [_result("Technical")]
    expected = [_expected("Technical")]
    m = compute_metrics(results, expected)
    report = render_report(m, Path("test.csv"))

    assert "F1 ≥ 0.70" in report


def test_render_report_includes_dataset_name() -> None:
    results = [_result("Technical")]
    expected = [_expected("Technical")]
    m = compute_metrics(results, expected)
    report = render_report(m, Path("my_eval_data.csv"))

    assert "my_eval_data.csv" in report
