# EXECUTION_PLAN.md — SOInsight build sequence

> Imported by CLAUDE.md. Build **one session = one phase**. Do not start a session until the previous
> session's **Done-when** checks all pass. Paste the **Prompt** into Claude Code to drive each session.

## How to run this

1. Open the repo in VS Code with the Claude extension. `CLAUDE.md` loads automatically.
2. Work top-to-bottom. Each session: paste the prompt → review the diff → run the **Done-when** checks → commit.
3. Keep sessions scoped. If Claude starts touching files from a later phase, stop it and refocus.
4. Use `#` in-session to capture any new durable rule, then move it into `CLAUDE.md` if it should persist.

---

## S0 — Bootstrap & guardrails
**Goal:** repo skeleton, config, logging, health, lint/type/test wiring. No business logic.
**Files:** `backend/app/{main.py,settings.py,logging.py,db.py,models.py}`, `backend/app/taxonomy.py`, `backend/pyproject.toml`, `backend/.env.example`, `backend/tests/test_taxonomy.py`, `.gitignore`.
**Prompt:**
> Scaffold the backend per CLAUDE.md §Stack and §Conventions. Create the pydantic-settings config (SO_BASE_URL, SO_API_KEY, SO_TEAM, OLLAMA_URL, DB paths), structlog JSON logging, SQLModel engine + the four tables in the data model, a FastAPI app with lifespan that runs an Ollama health check, and `/health` + `/health/deps`. Add `taxonomy.py` from docs/taxonomy.md verbatim plus `is_valid()`. Configure ruff + mypy + pytest in pyproject.toml. Write `test_taxonomy.py` asserting every sub-category validates and a fake label fails. No SO or LLM logic yet.

**Done-when:** `ruff check`, `mypy`, `pytest` all green · `uvicorn app.main:app` boots · `/health` returns 200 · `/health/deps` reports Ollama status · `.env.example` lists every var · `.env` is gitignored.

---

## S1 — SO Enterprise v3 client + connection test
**Goal:** authenticated, retrying, paging client + Private-Teams discovery. Read-only probe.
**Files:** `services/so_client.py`, `routers/settings.py`, `tests/test_so_client.py`.
**Prompt:**
> Implement `services/so_client.py` per docs/so-api.md: async httpx client, `SOAuth` (api_key mode), tenacity retry on 429/5xx/timeout, `pageSize=100` pagination helper, and methods `test_connection()`, `list_scopes()` (Private Teams/Communities), `iter_questions(tag, since, team)`, `list_tags()`. Add `routers/settings.py` with `POST /api/settings` (store config) and `GET /api/settings/test` (returns detected version + reachable scopes). **Do not hardcode param names** — leave a clearly-marked TODO to confirm date/page params against Swagger, with the best guess behind a constant. Mock httpx in `test_so_client.py` to test paging + retry + auth headers (assert User-Agent + X-API-Key present, key never logged).

**Done-when:** `GET /api/settings/test` against the real instance returns 200 + scope list · paging loop verified on mock · retry verified on mocked 429 · key absent from logs · tests green.

---

## S2 — Ingestion + storage + idempotency
**Goal:** pull tagged questions in a window into SQLite, safely re-runnable.
**Files:** `routers/questions.py`, ingestion service logic, `tests/test_ingest.py`.
**Prompt:**
> Add ingestion: `POST /api/questions/fetch` (body: products[], window_days) starts a background task that pulls via `iter_questions` across selected scopes, upserts into `questions` keyed by `so_id` (no duplicates on re-run), and streams progress over SSE `GET /api/questions/stream`. Track a daily-call budget and throttle. Map SO fields to the Pydantic/SQLModel row explicitly per docs/so-api.md. Test idempotency (run twice → same row count) and field mapping with mocked client.

**Done-when:** fetch fills `questions` · re-running doesn't duplicate · SSE emits progress · budget tracked · tests green.

---

## S3 — Embeddings + ChromaDB + dedup
**Goal:** embed questions, store vectors, detect near-duplicates.
**Files:** `services/embeddings.py`, `services/chroma_store.py`, `tests/test_chroma.py`.
**Prompt:**
> Implement `embeddings.py` (Ollama `nomic-embed-text`, retried) and `chroma_store.py` (persistent client, upsert keyed by so_id, `query_similar(text, k)`). During/after ingestion, embed `title + first 300 chars of body` and upsert. Add a duplicate check: if cosine similarity ≥ threshold to an existing question, flag candidate duplicate. Mock Ollama in tests; verify idempotent upsert and similarity wiring.

**Done-when:** embeddings persist across restart · re-upsert doesn't duplicate vectors · similarity query returns ranked ids · tests green.

---

## S4 — Classification engine (core)
**Goal:** assign one main + one sub from the fixed enum, with validation + confidence.
**Files:** `services/classifier.py`, `tests/test_classifier.py`.
**Prompt:**
> Implement `classifier.py` using Ollama `llama3.1:8b`. Build a prompt that includes the TAXONOMY enum and 2–3 few-shot examples per main category, instructs JSON-only output `{main, sub, confidence, reason}`, and is **constrained to the enum**. Parse + validate with `is_valid()`. On invalid: retry once with a stricter prompt; if still invalid, force `("Misuse / Noise","Incomplete or low-quality questions")` confidence 0.0 and log. Batch ~20 questions/call. Persist to `classifications`, keyed by question_id, re-runnable. Mark embedding-detected duplicates as `Misuse / Noise → Duplicate questions`. Tests (mock Ollama): valid passes, invalid triggers fallback, batch never crashes on one bad item, duplicates routed to noise.

**Done-when:** every question gets {main,sub,confidence,is_noise} · invalid output never crashes a batch · re-run is idempotent · tests green.

---

## S5 — Aggregation, patterns & recommendations
**Goal:** turn classifications into per-product patterns + matrix-driven actions.
**Files:** `services/aggregator.py`, `routers/analysis.py`, `tests/test_aggregator.py`.
**Prompt:**
> Implement `aggregator.py`: per product/tag and window, compute counts, category distribution, top recurring issues (exclude Misuse/Noise from headline counts but report noise volume). Form **patterns** only from clusters with **≥3 questions and ≥2 distinct users** (use embeddings to group within a sub-category). Per pattern, attach the suggested action from `RECOMMENDATION_MATRIX[main]`. Compute trend-over-time with a **minimum-volume guard** (skip spike flags below a configurable threshold). Derive author technical-vs-non-technical heuristically from `users/{id}` role/title; clearly label it approximate. Add `POST /api/analysis/start` + SSE `GET /api/analysis/stream`. Persist to `patterns`/`runs`. Test the ≥3/≥2 threshold and matrix mapping precisely.

**Done-when:** clusters below threshold are NOT patterns · each pattern has a matrix action · noise excluded from headline counts · trend guard suppresses low-volume spikes · tests green.

---

## S6 — Insights API for the dashboard
**Goal:** read endpoints the frontend consumes.
**Files:** `routers/insights.py`, `tests/test_insights.py`.
**Prompt:**
> Add read endpoints: `GET /api/insights/summary?product=&window=` (one summary per product/tag: top issues, category breakdown, key patterns, recommended actions, technical/non-technical split), `GET /api/insights/patterns`, `GET /api/insights/report?format=md|json` (Product-Owner export). Pure reads from SQLite. Test response shape against a seeded DB.

**Done-when:** summary returns the §9 output shape · report exports valid md + json · tests green.

---

## S7 — React frontend
**Goal:** non-technical-friendly dashboard fulfilling the §9 output.
**Files:** `frontend/src/{pages,components,api}/...`, `frontend/Dockerfile`, `frontend/package.json`.
**Prompt:**
> Build the Vite + React app: Settings (instance URL, api_key masked, team/scope select, Test Connection), Fetch (product + window pickers, SSE progress), Analysis (start + stage progress), Dashboard (per product/tag: category distribution + frequency charts via Recharts, top issues, key patterns with their recommended action, technical/non-technical split labelled approximate, filters for product + window, export button). Axios client + SSE helper in `api/`. Error boundaries + loading skeletons. No browser storage; state via React only.

**Done-when:** full flow works against the backend · charts render real data · export downloads · graceful loading/error states.

---

## S8 — Eval, tests, hardening
**Goal:** trust the classifier; cover the critical paths.
**Files:** `backend/eval/`, more tests.
**Prompt:**
> Add a classification eval harness: load a hand-labelled CSV (question, expected main/sub), run the classifier, report precision/recall/F1 per category + a confusion matrix, and write a markdown report. Document how to improve weak categories (more few-shot, or a larger local model). Raise backend coverage on so_client paging/retry, classifier fallback, aggregator thresholds, insights shape.

**Done-when:** `python -m eval` prints per-category metrics + report · weak spots documented · coverage on the four critical modules.

---

## S9 — Packaging & ship
**Goal:** one-command local run, documented.
**Files:** `backend/Dockerfile`, `docker-compose.yml`, `start.sh`, `README.md`.
**Prompt:**
> Multi-stage backend Dockerfile (non-root, healthcheck). `docker-compose.yml` for backend + frontend + ollama with a volume for chroma/sqlite. `start.sh` pulls `llama3.1:8b` + `nomic-embed-text` then `docker-compose up`. README: prerequisites, `.env` setup, run steps, the analysis workflow, and the eval. Verify a clean clone runs end-to-end.

**Done-when:** fresh clone → `./start.sh` → app reachable, full flow works · README is sufficient for a new engineer.

---

## S10 — Scheduled refresh (trends)
**Goal:** genuine trend-over-time tracking.
**Files:** scheduler config / cron.
**Prompt:**
> Add a scheduled job (cron or container) that re-runs ingestion + classification + aggregation per product on a cadence, appending to `runs` so trends accumulate. Make cadence + products configurable via settings. Ensure idempotency holds across scheduled runs.

**Done-when:** scheduled run refreshes data without duplication · trends reflect multiple runs over time.

---

## Definition of "production-ready" (final gate)

- [ ] `ruff` + `mypy` clean; tests green; classifier eval run with documented per-category metrics
- [ ] All config via `.env`; no secrets in code, logs, or git history; `.env.example` complete
- [ ] Every external call retried with backoff; no silent excepts; clear error states surfaced to UI
- [ ] Ingestion + classification + scheduled runs idempotent (verified by re-run tests)
- [ ] `/health` + `/health/deps` green; SSE progress on long jobs
- [ ] Taxonomy enforced (invalid labels impossible); patterns honor ≥3 Qs / ≥2 users; noise excluded from headline counts
- [ ] Agent only *recommends* — zero writes to external systems
- [ ] `docker-compose up` runs the full stack from a clean clone; README sufficient for handover
