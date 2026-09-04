#!/usr/bin/env bash
# Build the SOInsight classifier model and smoke-test it.
#
#   ./models/build.sh                    # default base (qwen2.5:3b-instruct)
#   ./models/build.sh llama3.2:3b        # different base
#
# Safe to re-run: `ollama create` replaces the existing tag.
set -euo pipefail

BASE="${1:-qwen2.5:3b-instruct}"
NAME="soinsight-classifier"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELFILE="$ROOT/models/Modelfile"

command -v ollama >/dev/null 2>&1 || { echo "ollama not found on PATH — see https://ollama.com"; exit 1; }
ollama list >/dev/null 2>&1 || { echo "ollama is installed but not running — start it and retry"; exit 1; }

echo "==> Pulling base model: $BASE"
ollama pull "$BASE"

echo "==> Generating Modelfile from backend/app/taxonomy.py"
python3 "$ROOT/models/generate_modelfile.py" --base "$BASE" --out "$MODELFILE"

echo "==> Building $NAME"
ollama create "$NAME" -f "$MODELFILE"

echo "==> Smoke test: one question, must return valid taxonomy JSON"
PROMPT='Classify this question. Reply with one JSON object only, keys main, sub, confidence, reason.
Title: Where is the OAuth setup guide?
Body: I cannot find any documentation on configuring OAuth for the internal platform.'

RESPONSE="$(ollama run "$NAME" --format json "$PROMPT" 2>/dev/null || true)"
echo "$RESPONSE"

python3 - "$RESPONSE" <<'PY'
import json, sys
sys.path.insert(0, __import__("os").path.join(__import__("os").getcwd(), "backend"))
raw = sys.argv[1].strip()
try:
    obj = json.loads(raw)
except json.JSONDecodeError:
    print("\nFAIL: response was not valid JSON. Try a different base model.")
    raise SystemExit(1)
try:
    from app.taxonomy import is_valid
except ImportError:
    print("\nOK: valid JSON (run from the repo root to also validate the taxonomy pair).")
    raise SystemExit(0)
main, sub = str(obj.get("main", "")), str(obj.get("sub", ""))
if not is_valid(main, sub):
    print(f"\nFAIL: {main!r} / {sub!r} is not a valid taxonomy pair.")
    raise SystemExit(1)
print(f"\nOK: {main} / {sub}")
PY

cat <<EOF

Built: $NAME

Point SOInsight at it:
  Settings -> Classification model -> $NAME
  (or set OLLAMA_MODEL=$NAME in backend/.env)

Score it against your own labelled data before trusting it:
  cd backend
  python -m eval.export_dataset --eval-csv eval/from_db.csv --exclude-noise
  python -m eval --csv eval/from_db.csv --out eval_report.md
EOF
