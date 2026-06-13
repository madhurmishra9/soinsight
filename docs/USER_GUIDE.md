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

Top-right of the Dashboard: **JSON** and **Markdown**. Reports include every
question (with links) under Top Issues, Key Patterns, an "All Questions by
Category" appendix, and a "Noise / Excluded Questions" section. Filenames
reflect the actual range used (e.g. `report_cloudsql_2026-01-01_to_2026-06-11.md`).

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
