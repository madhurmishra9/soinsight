"""
Entry point for `python -m eval` (run from the backend/ directory).

Usage:
    cd backend
    python -m eval
    python -m eval --csv eval/eval_data.csv --ollama http://localhost:11434 --out eval_report.md
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from eval.run_eval import run_eval


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m eval",
        description="Run the SOInsight classifier eval against a hand-labelled CSV.",
    )
    parser.add_argument(
        "--csv",
        default="eval/eval_data.csv",
        help="Path to the hand-labelled CSV (default: eval/eval_data.csv)",
    )
    parser.add_argument(
        "--ollama",
        default="http://localhost:11434",
        help="Ollama base URL (default: http://localhost:11434)",
    )
    parser.add_argument(
        "--out",
        default="eval_report.md",
        help="Output path for the Markdown report (default: eval_report.md)",
    )
    args = parser.parse_args()

    exit_code = asyncio.run(
        run_eval(
            csv_path=Path(args.csv),
            ollama_url=args.ollama,
            output_path=Path(args.out),
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
