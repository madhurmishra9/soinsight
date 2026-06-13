#!/usr/bin/env bash
# =============================================================================
# SOInsight — one-command launcher (macOS / Linux)
# chmod +x start-mac.sh   then   ./start-mac.sh        (single-process mode)
#                                ./start-mac.sh --dev   (hot-reload dev mode)
# Installs deps, writes config, builds the UI, starts ONE server, opens it.
# =============================================================================
set -euo pipefail

# ╔═══════════════════════ ONE-TIME CONFIG — edit these once ═══════════════════╗
SO_BASE_URL="https://stackenterprise.co/api/v3"   # must include /api/v3
SO_API_KEY="PASTE_YOUR_API_KEY_HERE"
SO_TEAM=""
OLLAMA_URL="http://localhost:11434"
OLLAMA_MODEL="llama3.1:8b"                             # must match `ollama list`
DEFAULT_TAGS="cloudsql,cloudspanner,cloudstorage"
ENABLE_SCHEDULE="true"
SCHEDULE_INTERVAL_HOURS="24"
SCHEDULE_WINDOW_DAYS="90"
# ╚═════════════════════════════════════════════════════════════════════════════╝

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
DEV_MODE="${1:-}"
echo "==> SOInsight launcher  ($ROOT)"

# 1) Python venv + backend deps
if [ ! -d ".venv" ]; then
  echo "==> Creating Python virtualenv..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip
pip install --quiet -e ./backend \
  || pip install --quiet -r backend/requirements.txt \
  || echo "WARN: could not auto-install backend deps"

# 2) Frontend deps
if [ ! -d "frontend/node_modules" ]; then
  echo "==> Installing frontend dependencies..."
  ( cd frontend && npm install --silent )
fi

# 3) Persistent settings
echo "==> Writing backend/.env..."
cat > backend/.env <<ENV
SO_BASE_URL=${SO_BASE_URL}
SO_API_KEY=${SO_API_KEY}
SO_TEAM=${SO_TEAM}
OLLAMA_URL=${OLLAMA_URL}
OLLAMA_MODEL=${OLLAMA_MODEL}
DEFAULT_TAGS=${DEFAULT_TAGS}
ENABLE_SCHEDULE=${ENABLE_SCHEDULE}
SCHEDULE_INTERVAL_HOURS=${SCHEDULE_INTERVAL_HOURS}
SCHEDULE_WINDOW_DAYS=${SCHEDULE_WINDOW_DAYS}
DB_PATH=./data/soinsight.db
CHROMA_PATH=./data/chroma
LOG_LEVEL=INFO
ENV

# 4) Ollama model
if command -v ollama >/dev/null 2>&1; then
  if ! ollama list 2>/dev/null | grep -q "${OLLAMA_MODEL%%:*}"; then
    echo "==> Pulling Ollama model ${OLLAMA_MODEL}..."
    ollama pull "${OLLAMA_MODEL}"
  fi
else
  echo "WARN: ollama not found — classification needs it (https://ollama.com)"
fi

unset SSL_CERT_FILE 2>/dev/null || true
mkdir -p backend/data

enable_schedule() {
  if [ "${ENABLE_SCHEDULE}" = "true" ]; then
    PRODS=$(printf '"%s",' ${DEFAULT_TAGS//,/ }); PRODS="[${PRODS%,}]"
    curl -sf -X PUT "$1/api/schedule" -H "Content-Type: application/json" \
      -d "{\"enabled\":true,\"interval_hours\":${SCHEDULE_INTERVAL_HOURS},\"products\":${PRODS},\"window_days\":${SCHEDULE_WINDOW_DAYS}}" \
      >/dev/null 2>&1 || true
  fi
}

if [ "$DEV_MODE" = "--dev" ]; then
  # ── DEV: hot-reload backend (8000) + Vite (5173) ─────────────────────────────
  echo "==> DEV mode: backend :8000 + frontend :5173"
  ( cd backend && "$ROOT/.venv/bin/python" -m uvicorn app.main:app --reload --port 8000 ) &
  BACK=$!
  for _ in $(seq 1 40); do curl -sf http://localhost:8000/health >/dev/null 2>&1 && break; sleep 1; done
  enable_schedule "http://localhost:8000"
  ( cd frontend && npm run dev ) &
  FRONT=$!
  sleep 3; open http://localhost:5173 2>/dev/null || xdg-open http://localhost:5173 2>/dev/null || true
  trap 'kill $BACK $FRONT 2>/dev/null || true; exit 0' INT TERM
  wait
else
  # ── PROD: build UI once, serve everything from ONE process on :8000 ─────────
  echo "==> Building UI (one-time per code change)..."
  ( cd frontend && npm run build --silent )
  echo "==> Starting SOInsight on http://localhost:8000 ..."
  ( cd backend && "$ROOT/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 ) &
  BACK=$!
  for _ in $(seq 1 40); do curl -sf http://localhost:8000/health >/dev/null 2>&1 && break; sleep 1; done
  enable_schedule "http://localhost:8000"
  open http://localhost:8000 2>/dev/null || xdg-open http://localhost:8000 2>/dev/null || true
  echo ""
  echo "============================================================"
  echo " SOInsight running (single process):  http://localhost:8000"
  echo " API docs:                            http://localhost:8000/docs"
  echo " Ctrl+C to stop."
  echo "============================================================"
  trap 'kill $BACK 2>/dev/null || true; exit 0' INT TERM
  wait
fi
