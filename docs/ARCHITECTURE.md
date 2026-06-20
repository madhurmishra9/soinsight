# SOInsight — Architecture

## Components

```
┌────────────────────────── localhost:8000 (single process) ─────────────────────────┐
│  FastAPI (uvicorn)                                                                  │
│  ├── /            → React SPA (built frontend, served as static files,             │
│  │                  path-traversal-safe — see SECURITY.md)                          │
│  ├── /api/settings  /api/questions  /api/analysis  /api/insights                   │
│  ├── /api/remediation  /api/patterns/dismiss  /api/runs  /api/schedule             │
│  ├── /api/insights/trends         (rising-volume detector)                          │
│  ├── /api/insights/tag-suggestions (untracked-tag discovery)                        │
│  ├── /api/insights/report?format=md|json|pdf                                        │
│  ├── SchedulerService (timed auto-fetch)                                            │
│  └── SQLite (backend/data/soinsight.db)                                             │
└──────────────┬───────────────────────────────────────────────┬─────────────────────┘
               │ HTTPS (bearer)                                 │ HTTP (localhost)
        SO Enterprise API v3                              Ollama /api/generate
        (questions, tags)                                 (classification LLM)
```

Dev mode (`--dev` / `-Dev`) runs the Vite dev server on :5173 with hot reload,
proxied to the backend on :8000.

## Data flow

1. **Ingestion** (`services/ingestion.py`)
   - Per tag: `since` = `MAX(created_at)` already in DB (incremental) or the
     selected window/custom range (full).
   - `services/so_client.py` paginates `/questions?tags=&fromdate=&todate=`,
     enforces the date range client-side, and stops early once a page crosses
     `since`.
   - `_map_question` maps SO v3 fields (camelCase, ISO dates, tag objects) into
     the `Question` table; upsert dedupes on `so_id`.

2. **Classification** (`routers/analysis.py` → `services/classifier.py`)
   - In-window questions are tag-filtered, then **pre-filtered** against the
     `classifications` table (single `IN` query) so only unclassified questions
     reach the LLM.
   - The classifier prompts Ollama (`settings.ollama_model`, switchable at
     runtime from Settings) to assign exactly one main + one sub-category from
     the fixed taxonomy; invalid outputs retry once then fall back to
     Misuse/Noise.

3. **Aggregation** (`services/aggregator.py`)
   - Counts per (main, sub), distinct users, technical-ratio heuristic, and
     persists patterns (≥3 questions from ≥2 distinct users).

4. **Insights** (`routers/insights.py`)
   - `_build_summary` is the single source for `/summary` (lean) and `/report`
     (rich: questions + links embedded per breakdown item, per pattern, and the
     noise list). It filters out patterns the analyst has snoozed unless
     `include_dismissed=true` is passed.
   - `/questions` powers every dashboard drill-down (category, sub-category,
     top issue, pattern, noise).
   - `/trends` compares a recent window (default 7d) against a trailing
     baseline window (default 30d) per (main, sub) and flags categories
     whose recent volume is ≥ `threshold`× the trailing average.
   - `/tag-suggestions` reads the cached instance-tag index populated by
     `/api/questions/validate-tags` and surfaces untracked tags ranked by
     instance-wide question count, with the local coverage ratio.
   - `/report?format=pdf` is rendered by `services/pdf_report.py` using
     reportlab Platypus — long tables and prose paginate cleanly across pages
     (table headers reprint via `repeatRows=1`, section headings stay attached
     to their first row via `KeepTogether`).

5. **Pattern dismissals** (`routers/dismissals.py`)
   - `POST /api/patterns/dismiss` snoozes a `(product, main, sub)` cluster for
     `days` (or `until`), or indefinitely if neither is set. Keyed by
     `(product, main, sub)` so snoozes survive window changes and re-aggregation.
   - `DELETE` restores; `GET` lists active dismissals. `active_dismissed_keys`
     is the helper consumed by `_build_summary`.

6. **Run history** (`routers/runs.py`)
   - `GET /api/runs` exposes the existing `runs` table newest-first, with
     parsed `products` and `counts`, computed `duration_seconds`, and
     `?status=…` / `?limit=` / `?offset=` filters.

## Date handling

`app/dates.py: resolve_range(window_days, from_date, to_date)` is the single
resolver used by ingestion, analysis, aggregation, and insights. Explicit dates
override the preset window; `to_date` is inclusive (end-of-day).

`app/dates.py` also exposes `utcnow()` and `utcfromtimestamp()` — naive-UTC
replacements for the deprecated `datetime.utcnow()` and `datetime.utcfromtimestamp()`.
Every production module uses them so the codebase is forward-compatible with
Python 3.14 (which removes the deprecated calls). Stored datetimes remain naive
to preserve schema compatibility with SQLite.

## Key tables

| Table | Purpose |
|-------|---------|
| `questions` | Raw SO questions (`so_id` unique, tags JSON, ISO dates) |
| `answers` | Answers per question (`so_id` unique, `question_so_id` index) |
| `classifications` | One row per classified question (`main_category`, `sub_category`, `is_noise`) |
| `patterns` | Persisted clusters per product/window with recommended action |
| `pattern_dismissals` | Per-(product, main, sub) snooze with optional `dismissed_until` and `reason` |
| `remediations` | Grounded LLM-generated fix guides per cluster, with cited evidence IDs |
| `runs` | Audit row per ingest/aggregate run (timings, status, JSON counts) |
| `schedule_config` | Singleton row holding the timed-fetch cadence and next-run pointer |

## Taxonomy

8 main categories (Product, Documentation, Operational, Awareness, Technical,
Security/Compliance, Adoption/Migration, Misuse/Noise) with fixed
sub-categories; Misuse/Noise is excluded from counts and patterns. Each main
category maps to one recommended action.
