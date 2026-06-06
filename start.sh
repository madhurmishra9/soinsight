#!/usr/bin/env bash
# start.sh — one-command bootstrap for SOInsight.
# Run from the repo root: ./start.sh
# Requires: Docker with Compose plugin (Docker Desktop >= 4.20 or docker-compose-plugin).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Pre-flight checks ──────────────────────────────────────────────────────────

if ! command -v docker &>/dev/null; then
  echo "ERROR: docker not found. Install Docker Desktop: https://docs.docker.com/desktop/"
  exit 1
fi

if [[ ! -f backend/.env ]]; then
  echo "ERROR: backend/.env not found."
  echo ""
  echo "  cp backend/.env.example backend/.env"
  echo "  # Then edit backend/.env and set SO_BASE_URL and SO_API_KEY."
  echo ""
  exit 1
fi

# ── Start Ollama first, pull models ───────────────────────────────────────────

echo "==> Starting Ollama..."
docker compose up -d ollama

echo "==> Waiting for Ollama to be ready (this may take 30 s on first start)..."
until docker compose exec -T ollama ollama list >/dev/null 2>&1; do
  sleep 3
done
echo "    Ollama is ready."

echo "==> Pulling llama3.1:8b  (≈4.7 GB — skipped if already present)..."
docker compose exec -T ollama ollama pull llama3.1:8b

echo "==> Pulling nomic-embed-text  (≈274 MB — skipped if already present)..."
docker compose exec -T ollama ollama pull nomic-embed-text

# ── Build and start the rest of the stack ────────────────────────────────────

echo "==> Building and starting backend + frontend..."
docker compose up --build -d

echo ""
echo "SOInsight is running:"
echo "  Dashboard : http://localhost:3000"
echo "  API docs  : http://localhost:8000/docs"
echo "  Health    : http://localhost:8000/health"
echo ""
echo "To follow logs : docker compose logs -f"
echo "To stop        : docker compose down"
echo "To run eval    : docker compose exec backend python -m eval"
