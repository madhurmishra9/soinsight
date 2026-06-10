# SOInsight

Ingests questions from an internal **Stack Overflow Enterprise** instance, classifies each into a
fixed taxonomy of pain-point categories, detects patterns across users, and produces structured
summaries for product owners. Runs entirely local — no hosted LLM, no external writes.

---

## Prerequisites

| Tool | Minimum version | Notes |
|---|---|---|
| Docker Desktop | 4.20+ (Compose plugin v2) | [docs.docker.com/desktop](https://docs.docker.com/desktop/) |
| RAM | 8 GB available to Docker | Ollama needs ~5 GB for `llama3.1:8b` |
| Disk | 10 GB free | LLM weights + app data |

> **Windows users:** run `start.sh` from WSL 2 or Git Bash (not PowerShell/CMD).

---

## Quick Start

```bash
# 1. Clone
git clone <repo-url> soinsight && cd soinsight

# 2. Configure (see Configuration below)
cp backend/.env.example backend/.env
# Edit backend/.env — set SO_BASE_URL and SO_API_KEY at minimum.

# 3. Start everything (pulls LLM weights on first run — ~5 GB download)
chmod +x start.sh
./start.sh
```

The dashboard is at **http://localhost:3000** once the stack is healthy.

---

## Configuration

Copy `backend/.env.example` to `backend/.env` and fill in:

| Variable | Required | Description |
|---|---|---|
| `SO_BASE_URL` | Yes | Your SO Enterprise API root, e.g. `https://acme.stackenterprise.co/api/v3` |
| `SO_API_KEY` | Yes | Read-only API key from SO Enterprise admin |
| `SO_TEAM` | No | Team/Community slug to scope ingestion (leave blank for main site) |
| `OLLAMA_URL` | Auto | Overridden to `http://ollama:11434` by Docker Compose — leave as-is |
| `DB_PATH` | Auto | Overridden to `/app/data/soinsight.db` by Docker Compose — leave as-is |
| `CHROMA_PATH` | Auto | Overridden to `/app/data/chroma` by Docker Compose — leave as-is |
| `LOG_LEVEL` | No | `INFO` (default) or `DEBUG` |

> The API key is never logged and never written to the database. Verify with `grep -r SO_API_KEY backend/app`.

---

## Analysis Workflow

Work through these steps in the dashboard:

### 1 — Settings
Open **Settings**, paste your SO Enterprise URL and API key, and click **Test Connection**. Detected
teams/communities are listed so you can choose which scopes to include.

### 2 — Fetch Questions
Open **Fetch**, pick one or more product tags and a time window (30 / 60 / 90 days), and click
**Fetch Questions**. Progress streams in real time. Questions are deduplicated — re-running is safe.

### 3 — Run Analysis
Open **Analysis** and click **Start Analysis**. The pipeline embeds and classifies every fetched
question, then clusters patterns. Patterns require ≥ 3 questions from ≥ 2 distinct users; anything
below that threshold is not surfaced as a pattern. Progress streams per stage.

### 4 — Dashboard
Open **Dashboard**, select a product/tag and window, and review:

- **Category breakdown** — distribution of pain-point types (bar chart).
- **Sub-category frequency** — top 8 sub-categories by volume (bar chart).
- **Top issues** — highest-frequency sub-categories.
- **Key patterns** — recurring clusters with a recommended action from the matrix. If no
  cluster meets the ≥3-questions/≥2-users threshold, the section explains why and lists any
  "emerging signals" that are close to qualifying — it never lowers the threshold or invents
  a pattern.
- **Technical/non-technical split** — heuristic author classification (labelled approximate).
- **Export** — download a Markdown or JSON report for the product owner, including every
  underlying question with a link back to the original post.

**Drill-down:** every category bar, sub-category bar, Top Issue row, and pattern card is
clickable. Clicking opens a side panel listing the exact questions behind that number, each
linking to the original question on your SO Enterprise instance.

**Dark mode:** toggle the theme from the button at the bottom of the sidebar. The choice is
remembered across reloads and defaults to your OS preference on first visit.

The agent surfaces recommended actions (e.g. "Update Backstage or Confluence") as text only and
never writes to any external system.

---

## Scheduled Refresh

SOInsight can automatically re-run the full pipeline (ingest → classify → aggregate) on a
configurable cadence so the dashboard always reflects recent questions and trend data accumulates
over time.

### Configure via API

```bash
# Enable a daily refresh of two product tags over a 30-day window:
curl -X POST http://localhost:8000/api/schedule \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "interval_hours": 24, "products": ["python", "api"], "window_days": 30}'

# Check current config and last/next run times:
curl http://localhost:8000/api/schedule
curl http://localhost:8000/api/schedule/status

# Fire a run immediately, ignoring the scheduled interval:
curl -X POST http://localhost:8000/api/schedule/trigger
```

| Field | Default | Description |
|---|---|---|
| `enabled` | `false` | Start/stop the scheduler without losing config |
| `interval_hours` | `24` | Cadence in hours (1–8760) |
| `products` | `[]` | Product tags to refresh (same tags used in Fetch) |
| `window_days` | `30` | Look-back window for each refresh (1–365) |

### How it works

- The scheduler runs inside the backend process as an asyncio background task, polling for a due
  run every 60 seconds.
- Each run is **idempotent**: questions are deduplicated by `so_id`, only unclassified questions
  are sent to the LLM, and patterns are upserted rather than appended.
- Every run appends a row to the `runs` table — the Dashboard uses these to compute trend direction
  (increasing / stable / decreasing) across windows.
- A concurrent run is skipped rather than stacked: if a run is in progress when the next interval
  fires, the new tick is silently dropped and retried at the next poll.

---

## Classification Eval

Measure how accurately the classifier labels questions against a hand-labelled ground-truth set:

```bash
# Run against the bundled 50-question eval set:
docker compose exec backend python -m eval

# Use a custom dataset:
docker compose exec backend python -m eval --csv eval/my_labels.csv --out eval_report.md
```

The eval CSV must have columns: `title`, `body`, `expected_main`, `expected_sub`.

The report (written to `eval_report.md` inside the container, also printed to stdout) includes:
- Per-category precision / recall / F1 with ⚠ flags for F1 < 0.70.
- A full confusion matrix.
- Per-category improvement advice (over-/under-predicted, contrastive examples to add).

To improve weak categories: add few-shot examples in `backend/services/classifier.py` (`_FEW_SHOT`),
or switch Ollama to `llama3.1:70b` for better zero-shot generalisation.

---

## Development (without Docker)

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --reload                        # http://localhost:8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev                                          # http://localhost:5173 (proxies /api to :8000)

# Tests
cd backend
pytest                          # 208 tests
ruff check .
mypy .
```

Ollama must be running locally (`ollama serve`) for classifier and embedding steps.

---

## Architecture

```
React (Vite · port 5173/3000)
  └─ nginx proxy or vite proxy ──> FastAPI (port 8000)
       ├─ SO Adapter ──────────────> Stack Overflow Enterprise v3 API
       ├─ Embeddings ──────────────> Ollama  nomic-embed-text
       ├─ ChromaDB  (dedup + cluster vectors)
       ├─ Classifier ──────────────> Ollama  llama3.1:8b
       ├─ Aggregator  (patterns ≥3 Qs / ≥2 users, recommendation matrix)
       ├─ Scheduler  (async background task — ingest→classify→aggregate on cadence)
       └─ SQLite  (questions · classifications · patterns · runs · schedule_config)
```

**Fixed taxonomy** — 8 main categories, 3–4 sub-categories each (see `docs/taxonomy.md`).
The classifier is constrained to this enum; invalid output triggers a retry then falls back to
`Misuse / Noise`. Patterns and recommendations are never derived from a single user's questions.

---

## Useful Commands

```bash
# View live logs
docker compose logs -f

# Restart only the backend after a code change
docker compose up --build -d backend

# Open a shell in the backend container
docker compose exec backend bash

# Stop everything (data volumes preserved)
docker compose down

# Stop and wipe all data (fresh start)
docker compose down -v

# Disable the scheduler without removing config
curl -X POST http://localhost:8000/api/schedule \
  -H "Content-Type: application/json" \
  -d '{"enabled": false, "interval_hours": 24, "products": ["python"]}'

# Fire a one-off refresh immediately
curl -X POST http://localhost:8000/api/schedule/trigger
```
