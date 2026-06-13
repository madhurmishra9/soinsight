# SOInsight — Architecture

## Components

```
┌────────────────────────── localhost:8000 (single process) ─────────────────────────┐
│  FastAPI (uvicorn)                                                                  │
│  ├── /            → React SPA (built frontend, served as static files)             │
│  ├── /api/settings  /api/questions  /api/analysis  /api/insights  /api/schedule    │
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
     noise list).
   - `/questions` powers every dashboard drill-down (category, sub-category,
     top issue, pattern, noise).

## Date handling

`app/dates.py: resolve_range(window_days, from_date, to_date)` is the single
resolver used by ingestion, analysis, aggregation, and insights. Explicit dates
override the preset window; `to_date` is inclusive (end-of-day).

## Key tables

| Table | Purpose |
|-------|---------|
| `questions` | Raw SO questions (`so_id` unique, tags JSON, ISO dates) |
| `classifications` | One row per classified question (`main_category`, `sub_category`, `is_noise`) |
| `patterns` | Persisted clusters per product/window with recommended action |

## Taxonomy

8 main categories (Product, Documentation, Operational, Awareness, Technical,
Security/Compliance, Adoption/Migration, Misuse/Noise) with fixed
sub-categories; Misuse/Noise is excluded from counts and patterns. Each main
category maps to one recommended action.
