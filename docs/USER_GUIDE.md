# SOInsight — User Guide

## 1. First-time setup (one time only)

1. Install prerequisites: **Python 3.11+**, **Node.js 18+**, **Ollama** (https://ollama.com).
2. Open the launcher for your OS in a text editor and fill the CONFIG block at the top:
   - `SO_API_KEY` — your Stack Overflow Enterprise bearer token
   - `SO_BASE_URL` — your instance URL **including** `/api/v3`
   - `DEFAULT_TAGS` — the product tags you care about
3. Run it:
   - macOS/Linux: `chmod +x start-mac.sh && ./start-mac.sh`
   - Windows: `powershell -ExecutionPolicy Bypass -File .\start-windows.ps1`

The launcher installs everything, builds the UI, starts **one** server, and opens
**http://localhost:8000**. The backend starts automatically with the UI — there is
nothing else to start.

## 2. Daily use

Just run the launcher again (or leave it running). Settings persist in
`backend/.env`; you never re-enter the API key.

### The four pages

| Page | What it does | When you need it |
|------|--------------|------------------|
| **Settings** | SO connection, Ollama URL, classification model picker | Once, or when rotating tokens / switching models |
| **Fetch Questions** | Pulls questions from SO into the local DB | When you want newer data. **Incremental by default** — only questions newer than the last fetch per tag are downloaded, so re-runs take seconds |
| **Analysis** | Classifies questions with the local LLM + detects patterns | After a fetch. **Always incremental** — already-classified questions are skipped automatically |
| **Dashboard** | Charts, top issues, patterns, drill-downs, exports | Anytime — it reads straight from the DB, no fetch/analysis needed to view existing results |

### Time selection (all pages)

- **Preset windows** — 7d / 14d / 30d / 60d / 90d buttons.
- **Custom range** — From/To date pickers. When set, they **override** the preset
  (the preset button visually deactivates). Clicking a preset clears the custom dates.

### Drill-downs (Dashboard)

Click any of these to see the underlying questions with links:
- a **Category** bar → all questions in that main category
- a **Sub-category** bar / **Top Issue** row / **Pattern** card → that exact cluster
- the **Noise volume** stat card → all excluded/low-quality questions

### Exports

Top-right of the Dashboard: **JSON**, **Markdown**, and **PDF**. All three
formats include every question (with links) under Top Issues, Key Patterns, an
"All Questions by Category" appendix, and a "Noise / Excluded Questions" section.
The PDF is produced with reportlab Platypus — long tables and remediation prose
paginate cleanly across pages, table headers reprint on every page, and section
titles stay attached to their first row. Filenames reflect the actual range used
(e.g. `report_cloudsql_2026-01-01_to_2026-06-11.pdf`).

### Snoozing a recurring pattern

Once a recurring `(product, main, sub)` cluster has been acknowledged or shipped
a fix, snooze it so it stops surfacing in Top Issues and the patterns list:

```bash
# Snooze for 30 days
curl -X POST http://localhost:8000/api/patterns/dismiss \
  -H "Content-Type: application/json" \
  -d '{"product":"cloudsql","main":"Technical","sub":"Reliability","days":30,"reason":"fix shipping in v1.4"}'

# List active snoozes for a product
curl 'http://localhost:8000/api/patterns/dismiss?product=cloudsql'

# Cancel
curl -X DELETE 'http://localhost:8000/api/patterns/dismiss?product=cloudsql&main=Technical&sub=Reliability'

# Show snoozed items in the next /summary call
curl 'http://localhost:8000/api/insights/summary?product=cloudsql&window=30&include_dismissed=true'
```

Snoozes are keyed by `(product, main, sub)`, so they survive window changes and
re-aggregation. Expired snoozes automatically stop hiding.

### Rising-volume detector

`GET /api/insights/trends?product=<tag>` compares a recent window (default 7d)
against a trailing baseline (default 30d) per category and flags categories
whose recent volume is ≥ 2× the trailing average. Tunable via `recent_days`,
`baseline_days`, `threshold`, and `min_recent` (noise floor — default 2).

### Tag auto-discovery

`GET /api/insights/tag-suggestions?tracked=<csv>` surfaces tags from your SO
instance that you aren't already tracking, ranked by instance-wide volume, with
a `coverage_ratio` showing how much of that tag's traffic you already have
locally. Reads the tag index cached by `/api/questions/validate-tags`, so the
first call after startup may be empty — type a tag in the Fetch page first to
prime the cache.

### Run history

`GET /api/runs` returns past ingest/aggregate runs newest-first, with
parsed products, JSON counts, status, and computed duration. Useful for
auditing the scheduled refresh or debugging a slow tag. Filter with
`?status=done|partial|failed|running` and paginate with `limit` / `offset`.

### Choosing a model

Settings → **Classification model** shows every model installed in Ollama.
Smaller models (e.g. `llama3.1:8b`) are much faster on CPU; larger models may
classify more accurately but take hours on big backlogs. Changing the model
only affects *new* classifications — existing ones are kept.

## 3. Scheduled auto-fetch

The launcher enables a timed fetch (default: every 24h over your `DEFAULT_TAGS`).
Data stays fresh without manual fetches. Adjust in the launcher CONFIG block.

## 4. Performance expectations

| Operation | First run | Subsequent runs |
|-----------|-----------|-----------------|
| Fetch (3 tags, 90d, ~16k questions) | ~1 hour | **seconds–minutes** (incremental) |
| Analysis (30 new questions, 8B model, CPU) | minutes | **seconds** if nothing new |
| Dashboard / exports | instant | instant (reads DB) |

## 5. Troubleshooting quick table

| Symptom | Fix |
|---------|-----|
| 401 Unauthorized | Token invalid/rotated — verify with `curl -H "Authorization: Bearer <key>" "<base>/tags?pageSize=100&page=1"` |
| `[Errno 2] No such file or directory` | Corporate `SSL_CERT_FILE` points at a missing file. The launchers handle this; for manual runs `unset SSL_CERT_FILE` first |
| Everything classified as noise + `/api/generate 404` | Model name doesn't match `ollama list` — pick a model in Settings |
| Settings blank after refresh | `backend/.env` missing — re-run the launcher |
| UI shows old code after replacing files | Re-run the launcher (it rebuilds the UI) |
