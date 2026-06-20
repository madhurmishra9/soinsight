# SOInsight

Internal **Stack Overflow Enterprise** intelligence platform. SOInsight ingests tagged questions from an SO Enterprise (API v3) instance, classifies each into a fixed taxonomy of pain-point categories using a **local LLM (Ollama)**, detects recurring patterns across users, and renders per-product insights, drill-downs, and exportable reports on a React dashboard.

**Fully local** — no hosted LLM, no telemetry, no writes to external systems. Question content never leaves your machine.

| Layer | Stack |
|---|---|
| Backend | Python 3.11 · FastAPI · SQLModel/SQLite · httpx · tenacity · structlog · Ollama · reportlab (PDF export) |
| Frontend | React 18 · Vite · TypeScript · Recharts · lucide-react |
| Inference | Ollama (classification LLM, switchable at runtime) + `nomic-embed-text` (embeddings/dedup) |

## Documentation

| Document | Contents |
|---|---|
| [docs/WHY_SOINSIGHT.md](docs/WHY_SOINSIGHT.md) | What SOInsight is and why you need it: the problem it solves, what it provides, how it compares to native Stack Overflow Enterprise analytics, other benefits, and honest limitations |
| [docs/BENEFITS.md](docs/BENEFITS.md) | Why SOInsight is automated: the one-command launcher, scheduled auto-fetch, the incremental/idempotent pipeline, local-first posture, and who benefits |
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | First-time setup, daily use, the four pages, time selection, drill-downs, exports, model selection, performance expectations, troubleshooting |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Component diagram, data flow (ingestion → classification → aggregation → insights), date handling, key tables, taxonomy |
| [docs/SECURITY.md](docs/SECURITY.md) | Data handling, secrets, network posture, auth model, dependency hygiene, known limitations |

---

## Requirements

| Component | Version / Notes |
|---|---|
| Python | 3.11+ |
| Node.js + npm | 18+ |
| Ollama | Installed and running locally ([ollama.com](https://ollama.com)). The launcher pulls the classification model automatically. |
| Shell | macOS/Linux: any POSIX shell · Windows: PowerShell |
| Network | Outbound HTTPS to your SO Enterprise instance; local access to Ollama (`http://localhost:11434`) |
| SO Enterprise | A bearer API token (Premium/Enterprise tier) — authenticated via `Authorization: Bearer <token>` |

Permissions:
- macOS/Linux: make the launcher executable (`chmod +x start-mac.sh`).
- Windows: allow the script to run (`-ExecutionPolicy Bypass`, shown below).
- Write access to the repo directory (the launcher creates `.venv`, `frontend/node_modules`, `backend/.env`, and `backend/data/`).

---

## Quick start (one command)

The simplest way — works the same on **macOS, Linux, and Windows** and needs nothing but Python 3 and Node.js installed:

```bash
python run.py
```

That single command, on first run, will:

1. Create a Python virtualenv (`.venv`) and install backend dependencies.
2. Install frontend dependencies (`npm install`).
3. Create `backend/.env` from the example if it doesn't exist (existing `.env` files are **never** overwritten — edit it to add your `SO_API_KEY`).
4. Start **both** the backend (`:8000`, hot-reload) **and** the frontend (`:5173`) together, stream their logs side by side, and open the UI.

Stop both with a single `Ctrl+C`.

Other modes:

```bash
python run.py --prod     # build the UI and serve everything from ONE process on :8000
python run.py --setup    # just install dependencies and exit
python run.py --no-open  # don't auto-open a browser
```

Prefer `make` or `npm`? These are equivalent (they call `run.py` under the hood, no extra dependencies):

```bash
make setup   |   make dev   |   make start
npm run setup   |   npm run dev   |   npm start
```

> On some systems Python is `python3`, not `python`. Use `python3 run.py` (or `make dev`, which defaults to `python3`).

### Alternative: the OS-specific launchers

`start-mac.sh` / `start-windows.ps1` do the same but also write `backend/.env` from a CONFIG block at the top of the script (handy if you want config baked into the launcher) and pull the Ollama model:

```bash
chmod +x start-mac.sh && ./start-mac.sh          # macOS/Linux (single process)
./start-mac.sh --dev                             # macOS/Linux (hot-reload, two ports)
powershell -ExecutionPolicy Bypass -File .\start-windows.ps1   # Windows
```

See the [User Guide](docs/USER_GUIDE.md) for the full first-time-setup and daily-use walkthrough.

---

## Configuration

All configuration lives in the CONFIG block at the top of each launcher, which is written to `backend/.env` (template: `backend/.env.example`):

| Variable | Required | Purpose |
|---|---|---|
| `SO_BASE_URL` | Yes | SO Enterprise API root, **including** `/api/v3` — e.g. `https://acme.stackenterprise.co/api/v3` |
| `SO_API_KEY` | Yes | Bearer API token (read-only) |
| `SO_TEAM` | No | Team/community slug to scope ingestion (blank = main site) |
| `OLLAMA_URL` | No | Ollama endpoint (default `http://localhost:11434`) |
| `OLLAMA_MODEL` | No | Classification model tag; must match `ollama list`. Also switchable at runtime from the Settings page. |
| `DEFAULT_TAGS` | No | Comma-separated tags seeded into the scheduled fetch |
| `FETCH_ANSWERS` | No | `true` (default) also ingests each new question's answers — one extra API call per new question |
| `ENABLE_SCHEDULE` | No | `true` enables the automatic timed fetch |
| `SCHEDULE_INTERVAL_HOURS` | No | Hours between scheduled fetches (default 24) |
| `SCHEDULE_WINDOW_DAYS` | No | Look-back window for scheduled fetches |
| `DB_PATH` / `CHROMA_PATH` | No | Storage locations (defaults under `backend/data/`) |
| `LOG_LEVEL` | No | `INFO` (default) or `DEBUG` |

Settings are persistent: they load from `backend/.env` on startup and survive restarts. The Settings page reloads the saved configuration on refresh.

> **Note:** save `backend/.env` as UTF-8 **without BOM** (the default in most editors). The backend tolerates a BOM, but some tooling does not.

**Security:** `backend/.env` and any launcher containing a real key hold the token in plaintext on local disk — both are git-ignored. The key is never logged, never returned by any API response, and never embedded in exports. Full details in [docs/SECURITY.md](docs/SECURITY.md).

---

## Analysis workflow

Work through the four pages in order (details and screenshots-level walkthrough in the [User Guide](docs/USER_GUIDE.md)):

1. **Settings** — paste your SO Enterprise URL and API key, click **Test Connection**, and pick a classification model from the models installed in Ollama.
2. **Fetch Questions** — pick product tags and a time window, click **Fetch**. Progress streams live (SSE). **Incremental by default**: only questions newer than the last fetch per tag are downloaded, so re-runs take seconds. Deduplicated by `so_id` — re-running is always safe.
3. **Analysis** — classifies every fetched question into exactly **one main + one sub-category** from the fixed taxonomy, then detects patterns. Already-classified questions are skipped automatically.
4. **Dashboard** — category/sub-category charts, top issues, key patterns, noise volume, and the technical/non-technical split (a tag heuristic, labelled APPROXIMATE). Every bar, row, and card drills down to the underlying questions with links back to your SO instance. Export **Markdown**, **JSON**, or **PDF** reports from the top right (PDF tables and prose paginate cleanly across pages).

Key behaviors:

- **Fixed taxonomy** — 8 main categories (Product, Documentation, Operational, Awareness, Technical, Security/Compliance, Adoption/Migration, Misuse/Noise), each with fixed sub-categories. The classifier is constrained to this enum; invalid LLM output retries once, then falls back to Misuse/Noise.
- **Pattern threshold** — a cluster is a pattern only at **≥ 3 questions from ≥ 2 distinct users**. Below the threshold the dashboard explains why and lists emerging signals — it never lowers the bar or invents a pattern.
- **Recommendations are text only** — the agent never writes to Confluence, Backstage, Jira, or ServiceNow.
- **Custom date ranges** — Fetch, Analysis, and the Dashboard accept `from_date`/`to_date` (`YYYY-MM-DD`); explicit dates override the preset 7/14/30/60/90-day windows.

---

## Scheduled auto-fetch

The scheduler runs inside the backend as an asyncio background task and re-runs the pipeline on a cadence. The launcher enables it from `DEFAULT_TAGS` / `SCHEDULE_INTERVAL_HOURS` / `SCHEDULE_WINDOW_DAYS`; it can also be managed from the UI or the API:

```bash
# Enable a daily refresh of two tags over a 30-day window
curl -X POST http://localhost:8000/api/schedule \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "interval_hours": 24, "products": ["cloudsql", "cloudstorage"], "window_days": 30}'

# Inspect config and last/next run times
curl http://localhost:8000/api/schedule
curl http://localhost:8000/api/schedule/status

# Fire a run immediately
curl -X POST http://localhost:8000/api/schedule/trigger
```

Runs are idempotent (dedup by `so_id`, only unclassified questions hit the LLM, patterns are upserted) and a tick that fires while a run is in progress is skipped, not stacked.

---

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/api/settings` | Read / store SO connection config (API key never echoed) |
| GET | `/api/settings/test` | Probe the SO instance; returns version + reachable scopes |
| GET | `/api/settings/ollama-models` | List models installed in local Ollama |
| POST | `/api/questions/fetch` | Start a background ingestion run |
| GET | `/api/questions/stream?run_id=` | SSE progress stream for an ingestion run |
| POST | `/api/analysis/start` | Start a classification + aggregation run |
| GET | `/api/analysis/stream?run_id=` | SSE progress stream for an analysis run |
| GET | `/api/insights/summary` | Per-product dashboard summary (snoozed patterns filtered unless `include_dismissed=true`) |
| GET | `/api/insights/patterns` | Detected patterns for a product/window |
| GET | `/api/insights/questions` | Questions behind a category / pattern / noise drill-down |
| GET | `/api/insights/trends` | Categories whose recent volume is ≥ threshold× the trailing baseline |
| GET | `/api/insights/tag-suggestions` | Untracked instance tags ranked by volume + local coverage |
| GET | `/api/insights/report` | Export report (`format=md`, `format=json`, or `format=pdf`) |
| POST / DELETE / GET | `/api/patterns/dismiss` | Snooze / restore / list dismissed `(product, main, sub)` clusters |
| POST | `/api/remediation/generate` | Start a grounded remediation run (SSE progress on `/api/remediation/stream`) |
| GET | `/api/remediation` | List stored grounded fix-guides for a product/window |
| GET | `/api/runs` | Past ingest/aggregate runs, newest-first |
| GET / POST | `/api/schedule` | Read / configure the scheduled fetch |
| POST | `/api/schedule/trigger` | Fire a scheduled run immediately |
| GET | `/api/schedule/status` | Scheduler state + last/next run |
| GET | `/health` | Process liveness |
| GET | `/health/deps` | Dependency health (Ollama reachability) |

Interactive API docs: **http://localhost:8000/docs**.

---

## Development

```bash
# Fastest: one command runs both (hot-reload backend + Vite UI)
python run.py

# …or run them by hand in two terminals:

# Backend
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn app.main:app --reload                        # http://localhost:8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev                                          # http://localhost:5173 (proxies /api to :8000)

# Quality gates
cd backend
pytest                          # 278 tests
ruff check .
mypy .
```

Ollama must be running locally (`ollama serve`) for classification and embedding steps. Architecture and data-flow details are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

### Classification eval

Measure classifier accuracy against a hand-labelled ground-truth set:

```bash
cd backend
python -m eval                                        # bundled 50-question eval set
python -m eval --csv eval/my_labels.csv --out eval_report.md
```

The CSV needs columns `title`, `body`, `expected_main`, `expected_sub`. The report includes per-category precision/recall/F1 (⚠ for F1 < 0.70), a confusion matrix, and improvement advice. To improve weak categories, add few-shot examples to `_FEW_SHOT` in `backend/services/classifier.py` or switch to a larger Ollama model.

---

## Docker Compose (alternative deployment)

Runs the full stack (Ollama + backend + nginx-served frontend) in containers.

| Tool | Minimum | Notes |
|---|---|---|
| Docker Desktop | 4.20+ (Compose v2) | [docs.docker.com/desktop](https://docs.docker.com/desktop/) |
| RAM | 8 GB available to Docker | Ollama needs ~5 GB for an 8B model |
| Disk | 10 GB free | LLM weights + app data |

```bash
# 1. Configure
cp backend/.env.example backend/.env
# Edit backend/.env — set SO_BASE_URL and SO_API_KEY at minimum.

# 2. Start everything (pulls LLM weights on first run — ~5 GB)
chmod +x docker-setup-start.sh
./docker-setup-start.sh          # Windows: run from WSL 2 or Git Bash
```

Dashboard: **http://localhost:3000** (the compose frontend is nginx on :3000; the API stays on :8000). Compose overrides `OLLAMA_URL`, `DB_PATH`, and `CHROMA_PATH` to container paths — leave those as-is in `.env`.

Useful commands:

```bash
docker compose logs -f                    # live logs
docker compose up --build -d backend     # rebuild backend after a code change
docker compose exec backend bash         # shell into the backend container
docker compose down                      # stop (data volumes preserved)
docker compose down -v                   # stop and wipe all data
```

---

## Architecture (overview)

```
React (Vite) ─ REST + SSE ─> FastAPI (127.0.0.1:8000, also serves the built SPA)
                               ├─> SO Adapter ──────> Stack Overflow Enterprise API v3 (bearer)
                               ├─> Classifier ──────> Ollama (local LLM, enum-constrained taxonomy)
                               ├─> Embeddings ──────> Ollama nomic-embed-text (dedup)
                               ├─> Aggregator        (patterns ≥3 Qs / ≥2 users, recommendation matrix)
                               ├─> Scheduler         (timed ingest → classify → aggregate)
                               └─> SQLite            (questions · classifications · patterns · runs · schedule_config)
```

Full component diagram, data flow, and table reference: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `401 Unauthorized` on `/api/v3/...` | Token rejected (or still the `.env` placeholder). Verify: `curl -H "Authorization: Bearer <key>" "<SO_BASE_URL>/tags?pageSize=100&page=1"` |
| Startup `ValidationError: … extra_forbidden` naming an env var | `backend/.env` was saved with a UTF-8 BOM by an editor — re-save without BOM (handled automatically on current code) |
| `[Errno 2] No such file or directory` at startup | A corporate `SSL_CERT_FILE` points to a missing path. The launchers handle it; for manual runs, `unset SSL_CERT_FILE` first |
| Everything classified as noise, `/api/generate 404` | `OLLAMA_MODEL` doesn't match `ollama list` — pick an installed model in Settings or `ollama pull <tag>` |
| Classification is very slow | The model runs on CPU. Smaller models (e.g. an 8B) are much faster; switch in Settings — existing classifications are kept |
| Settings blank after refresh | `backend/.env` missing — re-run the launcher, then restart the backend |
| Port 8000 already in use | A previous instance is still running — stop it (`Get-NetTCPConnection -LocalPort 8000` on Windows to find the PID) |

More in the [User Guide troubleshooting table](docs/USER_GUIDE.md#5-troubleshooting-quick-table).

---

## License

See [LICENSE](LICENSE).
