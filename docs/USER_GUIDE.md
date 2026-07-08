# SOInsight — User Guide

This guide covers every tab in the left-hand nav: what it does, what each input
means, and exactly how each number is calculated. There is no in-app help page —
this document is the single source of truth for that.

## Contents

1. [First-time setup](#1-first-time-setup-one-time-only)
2. [Daily use](#2-daily-use)
3. [How SOInsight fits together](#3-how-soinsight-fits-together)
4. [Settings](#4-settings)
5. [Fetch Questions](#5-fetch-questions)
6. [Analysis](#6-analysis)
7. [Dashboard](#7-dashboard)
8. [Rising trends](#8-rising-trends)
9. [Metrics](#9-metrics)
10. [Tag suggestions](#10-tag-suggestions)
11. [Snoozed](#11-snoozed)
12. [Run history](#12-run-history)
13. [State persistence across tabs](#13-state-persistence-across-tabs)
14. [Scheduled auto-fetch](#14-scheduled-auto-fetch)
15. [Performance expectations](#15-performance-expectations)
16. [Troubleshooting](#16-troubleshooting)

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

Just run the launcher again (or leave it running). Base URL, team, and Ollama
settings persist in `backend/.env` / server memory; the API key is stored
server-side only — see [Settings](#4-settings) for how re-saving other fields
never forces you to re-paste it.

## 3. How SOInsight fits together

SOInsight turns raw Stack Overflow Enterprise questions into recurring-problem
insights, in four stages that map to the left-hand nav, top to bottom:

1. **Settings** — point SOInsight at your SO instance and your local Ollama model.
2. **Fetch Questions** — pull tagged questions (and answers) from SO into the local database.
3. **Analysis** — classify each fetched question (main/sub category, noise or signal) and detect patterns.
4. **Insights** (Dashboard, Rising trends, Metrics, Tag suggestions, Snoozed) — read-only
   views over the classified data: nothing here calls Stack Overflow or the model
   except the Dashboard's on-demand *remediation guide* generation.

Nothing is computed live on page load beyond simple SQL aggregation — all
category counts, patterns, and trends are derived from rows already stored by a
Fetch + Analysis run. If a tab looks empty, the most common cause is: fetch
hasn't run for that tag/window, or fetch ran but analysis hasn't classified the
new questions yet (see [Metrics](#9-metrics) → Skipped / missing).

## 4. Settings

| Field | Meaning |
|---|---|
| **Base URL** | The root API URL of your SO Enterprise instance (e.g. `https://your-instance.stackenterprise.co/api/v3`). All question/tag/answer fetches are relative to this. |
| **API Key** | Sent as a bearer token on every SO API call. Stored in the backend's memory only for the life of the process — never written to disk or echoed back by any endpoint. **Re-saving other fields (team, Ollama URL, model) never clears a previously saved key** — leave the field blank and it keeps whatever is already stored server-side; only a non-blank value overwrites it. The page shows "A key is currently saved" once one exists, so it never *looks* like saving did nothing just because the field itself is empty. |
| **Team / Scope** | Optional. Scopes every SO call to one Team/Community (Private Teams-style instances). Leave blank to query the instance-wide public scope. |
| **Ollama URL / Classification model** | Where the local LLM used for classification and remediation lives, and which installed model to call. The dropdown is populated from Ollama's own `/api/tags`. |
| **Test connection** | Saves the form, then probes the instance: reachability, detected API version, and the list of Team/Community scopes visible to your API key. Use this after any change — **Fetch** and tag validation both reuse this same stored config. |

**Draft persistence:** whatever you've typed on this page (including an
in-progress, not-yet-saved API key) survives switching to another tab and back,
for the lifetime of the browser tab. It is intentionally **not** persisted to
disk/sessionStorage like the other pages' filters — a full page reload clears
the draft, the same tradeoff already made for the saved key itself, so a secret
never touches browser storage.

## 5. Fetch Questions

| Field | Meaning |
|---|---|
| **Tags to ingest** | One or more SO tags to pull questions for. Once a connection is established, use the **Browse tags** dropdown to pick from every tag your instance actually has (fetched live from SO, searchable — handles 500+ tags) instead of typing blind. Tags shown in red weren't found on the instance. Manual typing (Enter/comma to add) still works as a fallback. |
| **Time window / Custom date range** | Bounds on question *creation date*. A custom range overrides the quick-pick window. This only controls what's pulled from SO — Dashboard/Trends/Metrics windows are independent and applied afterwards against whatever is already stored locally. |
| **Incremental fetch** | When on, each tag resumes from its own `latest_question_at` watermark (the newest question already stored for that tag) instead of re-downloading the whole range — much faster for a daily refresh. Turn it off to force a full re-pull of the selected range (e.g. after changing the date range backward, or to pick up score/answer edits on existing questions). |
| **Local data coverage** | Read live from the database, not from SO: how many questions/answers you have stored per tag, the newest stored question's date ("data fetched till" — the incremental watermark), and when a fetch last completed for that tag. |

### Tag dropdown internals

`GET /api/questions/available-tags?search=&limit=&refresh=` returns every tag
name + instance-wide question count from the same cached tag index
`/api/questions/validate-tags` uses (10-minute TTL, refreshed on demand). It
reports `ok: false` (never an error) when the instance can't be reached, so the
UI falls back to manual entry instead of breaking.

## 6. Analysis

Analysis has two steps, run together: **classification** (the LLM assigns each
question a main/sub category, or flags it as `Misuse / Noise`) and
**aggregation** (grouping classified questions into patterns and category
counts for the given tag/window).

| Field | Meaning |
|---|---|
| **Tags / Time window / Custom date range** | Same semantics as Fetch — this is the set of already-fetched questions to classify and aggregate, not a new SO pull. |
| **Always incremental** | Only questions with no existing classification row are sent to the model — previously classified questions load instantly from the database. |
| **Results by tag** | *Signal questions* = classified as a real category (not noise). *Noise* = classified as `Misuse / Noise` (spam, duplicates, off-topic — excluded from every downstream count). *Patterns* = distinct (main, sub) clusters in this run that met the pattern threshold (see Dashboard below). |

The classifier always writes a Classification row once it processes a
question — even on repeated model failures it falls back to a
`Misuse / Noise` classification rather than leaving the question unprocessed
(see `services/classifier.py`). So a question with **no** Classification row at
all simply hasn't been through an Analysis run yet — that's exactly what
Metrics' "skipped / missing" count reports.

## 7. Dashboard

Everything on this tab is scoped to one product/tag and one window (quick-pick
days, or a custom date range), computed from classifications already stored by
Analysis.

| Stat / section | Meaning & calculation |
|---|---|
| **Signal questions** | Count of non-noise classified questions for this tag in the window. |
| **Noise volume** | Count classified `Misuse / Noise` — shown for visibility but excluded from every other stat, chart, and pattern on this page. Click the tile to see which questions. |
| **Patterns detected** | Number of (main, sub) clusters with **≥3 questions from ≥2 distinct users** in the window. Below that threshold a cluster shows up in the category breakdown / top issues, but not as a formal "pattern" — the threshold exists so one person's repeated asking doesn't look like a team-wide trend. |
| **Technical / Non-technical split** | **APPROXIMATE.** A question counts as "technical" if it carries at least one tag from a fixed technical-tag list (python, docker, kubernetes, api, sql, …) — a heuristic over question tags, not a verified attribute of the asking user. **Both bars are clickable** and open a drawer listing the actual questions behind that bucket (`GET /api/insights/technical-questions?technical=true|false`). |
| **Category distribution** | Bar chart of main-category totals. Click a bar to drill into its source questions. |
| **Sub-category frequency** | Bar chart of the **top 8** sub-categories by volume. Click a bar to drill in. |
| **Top issues** | The **top 8** (main, sub) pairs by question count, regardless of whether they cleared the pattern threshold — deliberately kept at the same limit as the frequency chart above so the two views never disagree about how many categories exist. |
| **Key patterns** | The qualifying clusters (see "Patterns detected" above), each with a suggested action drawn from a fixed recommendation matrix keyed by main category. |
| **Remediation guide** | On-demand, LLM-generated fix write-ups per qualifying pattern cluster — *the only thing on this page that calls the model live*. For each cluster the model is given the cluster's actual captured questions and answers and asked for a root cause, solution, and prevention plan, citing which question/answer IDs it used. **Grounded** means at least one cited question ID was verified to really belong to the cluster's captured sources — ungrounded output is discarded entirely and replaced with a neutral notice. Every question and answer referenced in a remediation card is tagged with its Stack Overflow ID (`[Q#12345]` / `[A#67890]`) so you can trace every claim back to the exact source post, both on this page and in JSON/Markdown/PDF exports. **Update guide** only (re)generates clusters whose source questions/answers changed since the last run (cheap). **Regenerate all** forces every cluster through the model again. |
| **Recommended actions** | Deduplicated list of suggested actions from Key patterns, ordered by how frequent the underlying pattern is. |
| **Export (JSON / Markdown / PDF)** | Full snapshot of everything above for the current product/window, including every source question, its stored answers, and the remediation guide if one has been generated. |

### Drill-downs

Click any of these to see the underlying questions with links (each tagged
with its Stack Overflow ID):
- a **Category** or **Sub-category** bar → all questions in that bucket
- a **Top Issue** row / **Pattern** card → that exact cluster
- the **Noise volume** stat card → all excluded/low-quality questions
- either **Technical / Non-technical** bar → the questions behind that split

## 8. Rising trends

Flags categories whose question volume just spiked, by comparing a short
recent window against a longer trailing baseline for the same tag.

| Field | Meaning & calculation |
|---|---|
| **Recent (days)** | The "is this spiking right now" window — counts every signal question classified into each (main, sub) category in the last N days. |
| **Baseline (days)** | The full look-back window used to establish a "normal" rate. Must be longer than Recent. The portion of the baseline *before* the recent window (i.e. `baseline_days − recent_days`) is the "trailing" period. |
| **Trailing avg / window** | The trailing period's question count, scaled to a window the same length as "Recent" — i.e. `(trailing_count / trailing_days) × recent_days` — so the comparison is apples-to-apples regardless of how long the baseline is. |
| **Multiplier** | `recent_count / max(trailing_avg, 1)`. A multiplier of 3.0× means the category is getting questions three times faster right now than its recent history predicts. |
| **Threshold ×** | The multiplier a category must reach or exceed to be flagged 🚨 Rising. Default 2.0×. |
| **Min recent** | A noise floor — a category needs at least this many recent questions to be flagged, even if its multiplier clears the threshold (protects against 0→1 questions registering as "infinite" growth). Default 2. |
| **The chart** | Each pair of bars is one category: the tall bar is the actual recent count, the muted bar is the trailing baseline average. Categories outlined in red cleared the rising threshold — the further the red bar towers over its paired gray bar, the sharper the spike. Sorted by multiplier, rising categories first. |

`GET /api/insights/trends?product=<tag>&recent_days=&baseline_days=&threshold=&min_recent=`

## 9. Metrics

An operational, pipeline-health view for a date range — separate from the
Dashboard's per-product insight view. Use it to answer "did the pipeline
actually process everything for this period, and how well is the product being
supported?" **Every number is clickable** and opens a side drawer listing the
exact questions behind it (backed by `GET /api/insights/metrics/questions?bucket=...`,
which reuses the identical filtering the summary itself uses, so a drawer's
contents never disagree with the number you clicked).

| Field | Meaning & calculation |
|---|---|
| **Total questions** | Every question stored locally with a creation date inside the selected range, across the tags you choose (or all known tags if none selected). |
| **Answered / Unanswered** | Answered = `answer_count > 0` on the stored question record (from SO at fetch time). Reflects SO's own answer count, independent of whether answer bodies were fetched (see `FETCH_ANSWERS`). |
| **Classified / analysed** | Questions in range that have at least one row in the classifications table — i.e. have actually been run through Analysis. |
| **Skipped / missing** | Questions fetched but with no classification row yet. The reason shown is "fetched but not yet processed by an Analysis run" — run Analysis for these tags/window to close the gap. |
| **People who asked** | Count of distinct `author_id` values across the in-range questions — how many different people were asking, not how many questions were asked. |
| **Got an accepted answer / Answered, still unresolved** | `Got an accepted answer` counts questions where one answer was marked accepted on Stack Overflow. `Answered, still unresolved` is the gap: questions with at least one answer but none accepted — often a sharper signal of unresolved pain than the raw "answered" count. |
| **Avg. answers / question, Avg. views / question** | Simple engagement/interest averages over every in-range question for the selected tags. |
| **Time to first answer** | Mean and median hours from a question's creation to its earliest captured answer. Only counts questions whose answer bodies were actually fetched (`FETCH_ANSWERS`) — a question with `answer_count > 0` but no stored answer rows is excluded here rather than skewing the average. |
| **Tag-wise breakdown** | The same totals (plus Accepted and Time to answer) broken out per tag, so you can see which product's data is stale, incomplete, or slow to support, at a glance. A question tagged with multiple tracked tags is counted once per matching tag, so column totals can exceed "Total questions." |

## 10. Tag suggestions

| Field | Meaning |
|---|---|
| **Tracked tags** | Tags you already fetch/track — excluded from the suggestion list. |
| **Instance volume** | Total question count for that tag on the SO instance overall (from the cached tag index — primed whenever a tag is fetched or validated on the Fetch page). |
| **Local count / Coverage** | How many of that tag's questions you've already stored locally, and `local_count / instance_count` as a percentage — low coverage on a high-volume untracked tag is the strongest "you're probably missing signal here" flag. |

`GET /api/insights/tag-suggestions?tracked=<csv>&min_instance_count=&limit=`

## 11. Snoozed

Lets you acknowledge a recurring `(product, main, sub)` pattern so it stops
cluttering the Dashboard's Key patterns / Top issues / Recommended actions
until the snooze expires (or indefinitely, if left blank). The underlying
questions and classifications are untouched — snoozing only hides the
pattern's presentation, and it reappears automatically the moment the snooze
lapses.

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
re-aggregation.

## 12. Run history

`GET /api/runs` returns past Fetch/Analysis runs newest-first: when it
started/finished, its status, which products/window it covered, how long it
took, and a summary counts object (e.g. patterns found, questions processed).
Use this to confirm a scheduled or manual run actually completed before
trusting the Dashboard/Metrics numbers for that period. Filter with
`?status=done|partial|failed|running` and paginate with `limit` / `offset`.

## 13. State persistence across tabs

Every page's filters and last-loaded results survive switching to another tab
and back — you never have to re-select a product, re-pick a window, or re-click
Load/Detect/Suggest just because you looked at a different tab in between.
Concretely:

- **Fetch, Analysis, Remediation** (`RunsContext`): form inputs, progress logs,
  and — crucially — the live SSE connection for an in-flight run. A fetch or
  analysis keeps streaming even while you're on a different page, and survives
  a full browser reload (it reconnects to the still-running backend job).
- **Dashboard, Rising trends, Metrics, Tag suggestions** (`PageStateContext`):
  filter inputs and the last successful response, mirrored to `sessionStorage`
  so a reload restores them too.
- **Settings** (`PageStateContext`, `settingsDraft` slice): your in-progress
  form, including an unsaved API key, kept in memory only — deliberately
  **not** written to `sessionStorage`, so a secret never touches browser
  storage; a full reload clears the draft (not the saved server-side config).

Only an explicit action — changing a filter and reloading, saving new
Settings, or a full browser refresh where noted above — changes what's shown.
Simply navigating the nav bar never does.

## 14. Scheduled auto-fetch

The launcher enables a timed fetch (default: every 24h over your `DEFAULT_TAGS`).
Data stays fresh without manual fetches. Adjust in the launcher CONFIG block.

## 15. Performance expectations

| Operation | First run | Subsequent runs |
|-----------|-----------|-----------------|
| Fetch (3 tags, 90d, ~16k questions) | ~1 hour | **seconds–minutes** (incremental) |
| Analysis (30 new questions, 8B model, CPU) | minutes | **seconds** if nothing new |
| Dashboard / Metrics / Trends / exports | instant | instant (reads DB) |

## 16. Troubleshooting

| Symptom | Fix |
|---------|-----|
| 401 Unauthorized | Token invalid/rotated — verify with `curl -H "Authorization: Bearer <key>" "<base>/tags?pageSize=100&page=1"` |
| `[Errno 2] No such file or directory` | Corporate `SSL_CERT_FILE` points at a missing file. The launchers handle this; for manual runs `unset SSL_CERT_FILE` first |
| Everything classified as noise + `/api/generate 404` | Model name doesn't match `ollama list` — pick a model in Settings |
| API key field looks empty after a reload | Expected — the key is never echoed back for security. If Settings shows "A key is currently saved," it's still configured server-side; you don't need to re-enter it unless rotating it. |
| Sub-category chart / Top issues show fewer categories than expected | Both are capped at the top 8 by volume (`_TOP_ISSUES_LIMIT` in `routers/insights.py`) — if fewer than 8 appear, fewer than 8 distinct categories actually exist for that product/window; check the category-distribution chart or category breakdown for the true count. |
| UI shows old code after replacing files | Re-run the launcher (it rebuilds the UI) |
