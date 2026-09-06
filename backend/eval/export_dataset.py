#!/usr/bin/env python3
"""
Export already-classified questions as evaluation and fine-tuning data.

Every Analysis run leaves behind (question -> main/sub) pairs in the database.
Once those have been spot-checked they are exactly the supervision a small model
needs, and they are labelled in *your* taxonomy against *your* products, which
no off-the-shelf model has seen.

Two outputs:

  --eval-csv   title,body,expected_main,expected_sub — the format
               `python -m eval` already consumes, so a candidate model can be
               scored before it is trusted.

  --jsonl      chat-format records (system / user / assistant) for LoRA
               fine-tuning. The assistant turn is the exact JSON object the
               classifier expects back, so a tuned model is trained on the real
               output contract rather than an approximation of it.

Usage (from backend/):
    python -m eval.export_dataset --eval-csv eval/from_db.csv --limit 300
    python -m eval.export_dataset --jsonl train.jsonl --min-confidence 0.8 --exclude-noise
    python -m eval.export_dataset --eval-csv holdout.csv --jsonl train.jsonl --split 0.2
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.models import Classification, Question
from app.taxonomy import TAXONOMY, is_valid

SYSTEM_PROMPT = (
    "You classify Stack Overflow Enterprise questions into a fixed taxonomy. "
    "Reply with a single JSON object containing the keys main, sub, confidence, "
    "and reason. Use only taxonomy values, exactly as written."
)


def _body(text: str | None, limit: int = 300) -> str:
    """Match the classifier's own truncation so training input mirrors serving input."""
    return " ".join((text or "").split())[:limit]


def load_labelled(
    session: Session,
    limit: int | None,
    min_confidence: float,
    exclude_noise: bool,
) -> list[dict[str, Any]]:
    """Question/classification pairs whose labels are valid in the current taxonomy.

    A row whose category is no longer in the taxonomy (renamed since it was
    classified) is skipped rather than exported — training on it would teach a
    label the application now rejects.
    """
    rows = session.exec(
        select(Question, Classification).where(Classification.question_id == Question.id)
    ).all()

    out: list[dict[str, Any]] = []
    skipped_invalid = 0
    for q, c in rows:
        if c.confidence < min_confidence:
            continue
        if exclude_noise and c.is_noise:
            continue
        if not is_valid(c.main_category, c.sub_category):
            skipped_invalid += 1
            continue
        out.append({
            "so_id": q.so_id,
            "title": (q.title or "").strip(),
            "body": _body(q.body),
            "expected_main": c.main_category,
            "expected_sub": c.sub_category,
            "confidence": round(c.confidence, 3),
        })

    if skipped_invalid:
        print(
            f"note: skipped {skipped_invalid} row(s) whose category is no longer "
            "in the taxonomy",
            file=sys.stderr,
        )

    out.sort(key=lambda r: r["so_id"])
    if limit is not None:
        out = out[:limit]
    return out


def write_eval_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["title", "body", "expected_main", "expected_sub"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in ("title", "body", "expected_main", "expected_sub")})


def write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            assistant = json.dumps({
                "main": r["expected_main"],
                "sub": r["expected_sub"],
                "confidence": r["confidence"],
                "reason": "",
            })
            f.write(json.dumps({
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Title: {r['title']}\nBody: {r['body']}"},
                    {"role": "assistant", "content": assistant},
                ]
            }) + "\n")


def summarise(rows: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["expected_main"]] = counts.get(r["expected_main"], 0) + 1
    lines = [f"{len(rows)} labelled example(s)"]
    for main in TAXONOMY:
        n = counts.get(main, 0)
        flag = "  <- no examples" if n == 0 else ""
        lines.append(f"  {n:>5}  {main}{flag}")
    thin = [m for m in TAXONOMY if counts.get(m, 0) < 10]
    if thin:
        lines.append(
            "\nCategories under 10 examples will stay weak after tuning — "
            "classify more questions in them before training:\n  " + "\n  ".join(thin)
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None, help="SQLite path (default: settings.db_path)")
    ap.add_argument("--eval-csv", type=Path, default=None, help="write eval CSV here")
    ap.add_argument("--jsonl", type=Path, default=None, help="write fine-tuning JSONL here")
    ap.add_argument("--limit", type=int, default=None, help="cap total exported rows")
    ap.add_argument("--min-confidence", type=float, default=0.0, help="drop labels below this")
    ap.add_argument("--exclude-noise", action="store_true", help="drop Misuse / Noise rows")
    ap.add_argument(
        "--split", type=float, default=0.0,
        help="hold out this fraction for --eval-csv; the rest goes to --jsonl "
             "(requires both outputs). Without it both files get every row, which "
             "would score a tuned model on its own training data.",
    )
    ap.add_argument("--seed", type=int, default=0, help="shuffle seed for --split")
    args = ap.parse_args(argv)

    if not args.eval_csv and not args.jsonl:
        ap.error("nothing to do: pass --eval-csv and/or --jsonl")
    if args.split and not (args.eval_csv and args.jsonl):
        ap.error("--split needs both --eval-csv and --jsonl")
    if not 0.0 <= args.split < 1.0:
        ap.error("--split must be in [0.0, 1.0)")

    from sqlalchemy import create_engine

    from app.settings import settings
    db_path = args.db or Path(settings.db_path)
    if not db_path.exists():
        print(f"no database at {db_path} — run a Fetch and an Analysis first", file=sys.stderr)
        return 1

    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        rows = load_labelled(session, args.limit, args.min_confidence, args.exclude_noise)

    if not rows:
        print("no labelled questions matched — try lowering --min-confidence", file=sys.stderr)
        return 1

    if args.split:
        random.Random(args.seed).shuffle(rows)
        cut = max(1, int(len(rows) * args.split))
        holdout, train = rows[:cut], rows[cut:]
    else:
        holdout = train = rows

    if args.eval_csv:
        write_eval_csv(holdout, args.eval_csv)
        print(f"wrote {args.eval_csv}  ({len(holdout)} rows)")
    if args.jsonl:
        write_jsonl(train, args.jsonl)
        print(f"wrote {args.jsonl}  ({len(train)} rows)")

    print()
    print(summarise(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
