# SOInsight — User Guide

Everything in the left-hand nav: what it does, what each input means, and exactly
how each number is calculated. There is no in-app help page — this document is
the single source of truth.

## Contents

**Getting started**
1. [What SOInsight actually does](#1-what-soinsight-actually-does)
2. [Install and first run](#2-install-and-first-run)
3. [Your first 10 minutes](#3-your-first-10-minutes)
4. [The classification taxonomy](#4-the-classification-taxonomy)

**The tabs**

5. [Settings](#5-settings) · 6. [Fetch Questions](#6-fetch-questions) · 7. [Analysis](#7-analysis)
8. [Dashboard](#8-dashboard) · 9. [Rising trends](#9-rising-trends) · 10. [Metrics](#10-metrics)
11. [Tag suggestions](#11-tag-suggestions) · 12. [Snoozed](#12-snoozed) · 13. [Run history](#13-run-history)

**Reference**

14. [Windows, date ranges, and the one gotcha](#14-windows-date-ranges-and-the-one-gotcha)
15. [Exports](#15-exports)
16. [State persistence across tabs](#16-state-persistence-across-tabs)
17. [Performance expectations](#17-performance-expectations)
18. [Troubleshooting](#18-troubleshooting)

---

## 1. What SOInsight actually does

SOInsight turns raw Stack Overflow Enterprise questions into recurring-problem
insights. It runs in four stages that map to the nav, top to bottom:

| Stage | Tabs | What happens |
|---|---|---|
| **Connect** | Settings | Point at your SO instance and your local Ollama model. |
| **Ingest** | Fetch Questions | Pull tagged questions (and their answers) from SO into a local SQLite DB. |
| **Understand** | Analysis | A local LLM classifies each question into a fixed taxonomy, then clusters them into patterns. |
| **Act** | Dashboard, Rising trends, Metrics, Tag suggestions, Snoozed | Read-only views over the classified data. |

Two properties worth internalising, because they explain most surprises:

**Nothing is live except what you explicitly trigger.** Insight tabs read rows
already in the database. They never call Stack Overflow and never call the model.
The single exception is the Dashboard's **Remediation guide**, which calls the
model on demand when you click Generate. If a tab looks empty, it is almost
always because Fetch hasn't run for that tag/window, or Fetch ran but Analysis
hasn't classified the new questions yet.

**Every stage is idempotent.** Ingestion dedupes by SO question ID,
classification skips questions that already have a classification row, and
aggregation upserts patterns. Re-running anything is safe and cheap — nothing
double-counts.

---

## 2. Install and first run

**Prerequisites:** Python 3.11+, Node.js 18+, and [Ollama](https://ollama.com)
running locally. You also need a Stack Overflow Enterprise bearer API token.

### The one-command path

```bash
python run.py
```

On first run this creates a virtualenv, installs backend and frontend
dependencies, creates `backend/.env` from the example, then starts the backend
(`:8000`) and frontend (`:5173`) together and opens the UI. `Ctrl+C` stops both.

An existing `backend/.env` is **never** overwritten — edit it to add your
`SO_API_KEY`, or paste the key into Settings once the UI is up.

Other modes:

```bash
python run.py --prod      # build the UI, serve everything from one process on :8000
python run.py --setup     # install dependencies and exit
python run.py --no-open   # don't auto-open a browser
```

`make dev` / `npm run dev` are equivalent — they call `run.py` underneath. On
systems where Python is `python3`, use `python3 run.py`.

### The OS launcher path

`start-mac.sh` and `start-windows.ps1` do the same, and additionally write
`backend/.env` from a CONFIG block at the top of the script and pull the Ollama
model for you. Useful if you want configuration baked into the launcher.

```bash
chmod +x start-mac.sh && ./start-mac.sh                          # macOS/Linux
powershell -ExecutionPolicy Bypass -File .\start-windows.ps1     # Windows
```

Fill the CONFIG block first: `SO_API_KEY`, `SO_BASE_URL` (**including**
`/api/v3`), and `DEFAULT_TAGS`.

### Daily use

Run the launcher again, or just leave it running. Base URL, team, and Ollama
settings persist; the API key is stored server-side in memory only — see
[Settings](#5-settings).

---

## 3. Your first 10 minutes

1. **Settings** → enter Base URL and API Key → **Test connection**. You want
   "Reachable" and, on a Teams instance, your scopes listed.
2. **Fetch Questions** → click **Browse tags**, pick 1–3 real tags → choose a
   window (start with 30d) → **Fetch questions**. Watch the progress log.
3. **Analysis** → the same tags and **the same window** → **Start analysis**.
   This is the slow step; the local model classifies each new question.
4. **Dashboard** → pick the product → **the same window** → **Load**.

Steps 2–4 must agree on the window. Section
[14](#14-windows-date-ranges-and-the-one-gotcha) explains exactly why, and what
happens if they don't.

---

## 4. The classification taxonomy

Every question is classified into exactly one **main category** and one
**sub-category** from this fixed list. The model cannot invent categories —
output that doesn't match a valid pair is rejected and retried, then falls back
to `Misuse / Noise`.

| Main category | Sub-categories | Default recommended action |
|---|---|---|
| **Product** | Feature Gap · User / Developer Experience Gap · Integration Gap · Demand Signal | Add feature or improvement |
| **Documentation** | Missing Documentation · Unclear or poorly explained · Conflicting Information · Information spread across multiple sources | Update Backstage or Confluence |
| **Operational** | Configuration Complexity · Setup or deployment issues · Environment constraints · Lack of troubleshooting support | Improve setup, runbooks, or automation |
| **Awareness** | Feature not known · Incorrect assumptions about capability · Poor communication of changes or releases | Improve communication or release notes |
| **Technical** | Reliability issues or instability · Performance or scaling issues · Poor error handling or failures | Fix or optimise |
| **Security / Compliance** | Access control or permissions confusion · Network or connectivity issues · Data protection or encryption questions · Compliance or regulatory gaps | Align with security standards or guardrails |
| **Adoption / Migration** | Migration challenges between platforms/products · Breaking changes or upgrades · Difficulty getting started · Compatibility issues | Improve migration guides or tooling |
| **Misuse / Noise** | Incorrect usage · Duplicate questions · Incomplete or low-quality questions | *(none — excluded everywhere)* |

**Signal vs. noise.** Anything classified `Misuse / Noise` is excluded from every
count, chart, pattern, and recommendation downstream. It is reported separately
as "Noise volume" so you can see how much was filtered, and clicking that number
shows you exactly which questions were dropped.

**Duplicate detection.** If embeddings are configured, near-duplicate questions
are detected by vector similarity before the model is called and marked
`Misuse / Noise → Duplicate questions` with confidence 1.0, bypassing the LLM.

---

## 5. Settings

### Instance connection

| Field | Meaning |
|---|---|
| **Base URL** | Root API URL of your SO Enterprise instance, e.g. `https://your-instance.stackenterprise.co/api/v3`. Every question, tag, and answer fetch is relative to this. |
| **API Key** | Sent as a bearer token on every SO call. Held in backend memory only for the life of the process — never written to disk, never echoed back by any endpoint. |
| **Team / Scope** | Optional. Scopes every call to one Team/Community. Leave blank for the instance-wide public scope. |
| **Ollama URL** | Where the local model lives. Default `http://localhost:11434`. |
| **Classification model** | Which installed Ollama model to use. The dropdown is populated live from Ollama's own `/api/tags`; if Ollama is unreachable it degrades to a free-text field. Selecting "— keep current —" leaves it unchanged. |

**The API key never needs re-pasting.** Leave the field blank and whatever key
is already stored server-side survives — only a non-blank value overwrites it.
When a key exists, the page says *"A key is currently saved on the server —
leave this blank to keep it."* So re-saving the team or model never silently
wipes your key, and an empty-looking field does not mean the key is gone.

**Test connection** saves the form first, then probes the instance and reports
reachability, detected API version, and every Team/Community scope your key can
see. Use it after any change — Fetch and tag validation reuse this same stored
config.

### Scheduled auto-fetch

The card at the bottom controls a background job that runs
**Fetch → Analysis → Aggregate** on an interval, so data stays fresh without
visiting those tabs by hand.

| Field | Meaning |
|---|---|
| **Enable scheduled auto-fetch** | Master switch. The backend's scheduler loop always runs and polls every 60s; this flag is what it checks before doing anything. |
| **Interval (hours)** | How often a run fires, measured from the end of the previous run. 1–8760. |
| **Window (days)** | Look-back window each scheduled run uses for both the Fetch and Aggregate steps. 1–365. |
| **Tags to auto-fetch** | Chip multi-select over your known tags. Nothing runs for unselected tags. If the list is empty, fetch a tag manually once on the Fetch page and it will appear here. |
| **Save schedule** | Persists the config. Takes effect on the next poll — no restart needed. |
| **Run now** | Saves the current form first, then fires an immediate one-off run regardless of the interval. Disabled until at least one tag is selected. |
| **Status row** | Enabled/Disabled, whether a run is in progress right now, and last/next run timestamps. Refreshes automatically every 30s while the page is open. |

Because every stage is idempotent, an overlapping or repeated scheduled run
never double-counts anything. A run that is already in progress is skipped
rather than started twice.

### Draft persistence

Whatever you have typed here — including an unsaved API key — survives switching
tabs and coming back. It is deliberately **not** written to browser storage, so
a secret never touches `sessionStorage`; a full page reload therefore clears the
draft (but not the saved server-side config). The Scheduled auto-fetch card has
no secrets, so it uses the normal reload-surviving persistence.

---

## 6. Fetch Questions

Pulls tagged questions and their answers from Stack Overflow into the local
database. This is the only tab that downloads from SO.

| Field | Meaning |
|---|---|
| **Tags to ingest** | One or more SO tags. Click **Browse tags** for a searchable dropdown of every tag your instance actually has, with per-tag question counts. Typing also works — press Enter or comma to add, Backspace on an empty input to remove the last chip. |
| **Time window** | Quick-pick bound on question *creation date*: 7 / 14 / 30 / 60 / 90 days. |
| **Custom date range** | Overrides the quick-pick window when set. |
| **Incremental fetch** | On by default. Each tag resumes from its own watermark — the newest question already stored for that tag — instead of re-downloading the whole range. |
| **Local data coverage** | Read live from the database: questions and answers stored per tag, the newest stored question's date, and when a fetch last completed for that tag. |

**Tags shown in red** were not found on your instance — usually a typo. A tag
that simply couldn't be verified (SO unreachable) stays the normal colour, so a
valid tag is never wrongly flagged.

**When to turn incremental off.** Turn it off to force a full re-pull of the
selected range: after moving the date range backward, or to pick up score and
answer-count edits on questions you already stored. Incremental only ever looks
*forward* from the watermark, so it will not notice changes to existing
questions.

**Reading the coverage table.** "Data fetched till" is the incremental
watermark — the point up to which your local data is current for that tag.
"Last fetch run" is when a fetch job covering that tag last finished, which can
be more recent than the watermark if the run found nothing new.

**The tag dropdown** loads up to 5,000 tags from a cached index (10-minute TTL,
with a refresh button), filters as you type, and displays the top 300 matches —
refine your search if you see the "showing top 300" notice. If the instance
can't be reached, the panel says so and manual entry still works.

This tab only controls what is pulled *from SO*. Dashboard, Trends, and Metrics
windows are independent and are applied afterwards against whatever is already
stored locally.

---

## 7. Analysis

Two steps run together: **classification** (the LLM assigns each question a
main/sub category, or flags it as noise) and **aggregation** (grouping
classified questions into patterns and category counts).

| Field | Meaning |
|---|---|
| **Tags** | Which already-fetched questions to classify and aggregate. This is not a new SO pull. |
| **Time window / Custom date range** | Same semantics as Fetch. |

**Always incremental.** Only questions with no existing classification row are
sent to the model. Previously classified questions load instantly from the
database, so re-running Analysis after a small fetch costs seconds.

**Results by tag** reports, per tag: *Signal questions* (classified into a real
category), *Noise* (classified `Misuse / Noise`), and *Patterns* (clusters that
met the threshold in this run).

The classifier always writes a classification row once it processes a question —
even after repeated model failures it falls back to `Misuse / Noise` rather than
leaving the question unprocessed. So a question with **no** row at all simply
hasn't been through an Analysis run. That is exactly what Metrics'
"Skipped / missing" counts.

Analysis keeps running if you switch tabs, and survives a browser reload.

---

## 8. Dashboard

Everything here is scoped to one product/tag and one window, computed from
classifications already stored by Analysis.

### The stat row

| Stat | Meaning |
|---|---|
| **Signal questions** | Non-noise classified questions for this tag in the window. |
| **Noise volume** | Questions classified `Misuse / Noise` — shown for visibility, excluded from everything else on the page. **Click it** to see exactly what was excluded. |
| **Patterns detected** | Clusters with **≥3 questions from ≥2 distinct users**. |
| **Technical questions** | Percentage — see the caveat below. |

The pattern threshold exists so one person asking repeatedly doesn't look like a
team-wide trend. Below the threshold a cluster still appears in the category
breakdown and top issues, just not as a formal pattern.

### Charts and lists

| Section | Meaning |
|---|---|
| **Category distribution** | Main-category totals. Click a bar to drill into its questions. |
| **Sub-category frequency** | **Top 8** sub-categories by volume. Click a bar to drill in. |
| **Top issues** | **Top 8** (main, sub) pairs by question count, regardless of whether they cleared the pattern threshold. Deliberately the same limit as the chart above, so the two never disagree about how many categories exist. |
| **Technical / Non-technical split** | **APPROXIMATE.** A question counts as "technical" if it carries at least one tag from a fixed technical-tag list (python, docker, kubernetes, api, sql, …). This is a heuristic over *question tags* — it is not a verified attribute of the asking user. Both bars are clickable. |
| **Key patterns** | Qualifying clusters, each with a suggested action from the recommendation matrix in [§4](#4-the-classification-taxonomy). |
| **Recommended actions** | Deduplicated suggested actions from Key patterns, ordered by how frequent the underlying pattern is. |

**Emerging signals.** When no cluster clears the threshold but signal questions
exist, Key patterns instead shows the top 3 categories with ≥2 questions,
labelled *"Emerging signals (below pattern threshold)"*. These are clickable
too. It's an early-warning view of what's forming before it qualifies.

### Drill-downs

Click any of these to open a side drawer listing the underlying questions, each
linked and tagged with its Stack Overflow ID:

- a **Category** or **Sub-category** bar → all questions in that bucket
- a **Top issue** row or **Pattern** card → that exact cluster
- the **Noise volume** stat → everything excluded as noise
- either **Technical / Non-technical** bar → the questions behind that split

### Remediation guide

On-demand, LLM-generated fix write-ups per qualifying cluster. **This is the only
thing on this page that calls the model live.**

For each cluster the model receives that cluster's actual captured questions and
their actual stored answers (up to 15 questions, 4 answers each) and is asked for
a root cause, a solution, and a prevention plan — citing the question and answer
IDs it used.

**Grounding is enforced structurally, not by trust.** Every ID the model cites is
intersected with the cluster's real source IDs; anything invented is discarded. A
card is marked **grounded** only if at least one cited question ID survives. If
none does, the model's prose is thrown away entirely and replaced with a neutral
notice — you never see ungrounded content presented as a fix. Each card shows its
surviving evidence, so every claim traces back to a real `[Q#12345]` / `[A#67890]`.

| Button | Behaviour |
|---|---|
| **Generate guide** / **Update guide** | Only (re)generates clusters whose source questions/answers changed since the last run. Cheap to re-run. |
| **Regenerate all** | Forces every cluster through the model again. |

Changing the classification model also invalidates the cache, because the model
name is part of the content hash — so switching models and clicking *Update
guide* does regenerate everything.

If nothing comes back grounded, the usual cause is that no answer bodies were
captured. Enable `FETCH_ANSWERS`, re-fetch, and regenerate.

Generation keeps running if you switch tabs.

---

## 9. Rising trends

*(Page header reads "Rising-volume detector".)* Flags categories whose question
volume just spiked, by comparing a short recent window against a longer trailing
baseline for the same tag.

| Field | Meaning & calculation |
|---|---|
| **Recent (days)** | The "is this spiking right now" window. Counts signal questions per (main, sub) category in the last N days. |
| **Baseline (days)** | The full look-back used to establish a normal rate. **Must be longer than Recent.** |
| **Trailing avg / window** | The period *before* the recent window (`baseline_days − recent_days`), scaled to a window the same length as Recent: `(trailing_count / trailing_days) × recent_days`. This makes the comparison apples-to-apples regardless of baseline length. |
| **Multiplier** | `recent_count / max(trailing_avg, 1)`. 3.0× means questions are arriving three times faster than recent history predicts. |
| **Threshold ×** | The multiplier needed to be flagged 🚨 Rising. Default 2.0×. |
| **Min recent** | Noise floor: a category needs at least this many recent questions to be flagged, even if its multiplier clears the threshold. Stops 0→1 registering as infinite growth. Default 2. |

**The chart.** Each pair of bars is one category — the coloured bar is the actual
recent count, the muted bar is the trailing baseline. Rising categories are red
with a dark outline. Shows the top 15 by multiplier; the tables below list
everything, split into 🚨 **Rising** and **Steady**.

Empty results mean there are no classifications in the baseline window for that
tag. Try a longer baseline, a different product, or run Fetch and Analysis first.

---

## 10. Metrics

An operational, pipeline-health view for a date range — separate from the
Dashboard's per-product insight view. Use it to answer *"did the pipeline
actually process everything for this period, and how well is this product being
supported?"*

Leave **Tags** empty to cover every tag present in the range.

| Metric | Meaning & calculation | Clickable |
|---|---|---|
| **Total questions** | Every locally stored question created inside the range, across the selected tags. | ✔ |
| **Answered / Unanswered** | `answer_count > 0` on the stored record. Reflects SO's own count, independent of whether answer *bodies* were fetched. | ✔ |
| **Classified / analysed** | Questions with at least one classification row — i.e. actually run through Analysis. | ✔ |
| **People who asked** | Distinct author IDs — how many different people were asking, not how many questions. | — |
| **Got an accepted answer** | Questions where an answer was marked accepted on SO. Sub-label shows the rate as a share of *answered*. | ✔ |
| **Answered, still unresolved** | Has at least one answer, none accepted. Often a sharper signal of unresolved pain than the raw answered count. | ✔ |
| **Avg. answers / question** | Engagement per question. | — |
| **Avg. views / question** | Interest per question. | — |
| **Time to first answer** | Mean (and median, in the sub-label) hours from creation to the earliest *captured* answer. | ✔ |
| **Skipped / missing** | Fetched but with no classification row yet. Run Analysis for these tags and window to close the gap. When it's zero you get a green "analysis is fully caught up" banner instead. | ✔ |

Clicking a metric opens a side drawer listing the exact questions behind it,
using the identical filtering the summary itself uses — so a drawer's contents
can never disagree with the number you clicked. The time-to-answer drawer
additionally badges each question with how long it took.

**Time to first answer only counts questions whose answer bodies were actually
fetched.** A question with `answer_count > 0` but no stored answer rows is
excluded rather than skewing the average. If this reads `—`, you likely have
`FETCH_ANSWERS` disabled.

**Tag-wise breakdown** repeats the totals per tag, plus Accepted and Time to
answer, so you can see at a glance which product's data is stale, incomplete, or
slow to support. Every cell is clickable. Note that a question tagged with
several tracked tags is counted once *per matching tag*, so column totals can
legitimately exceed "Total questions".

---

## 11. Tag suggestions

*(Page header reads "Tag auto-discovery".)* Surfaces tags on your instance that
you aren't tracking yet, ranked by instance-wide volume.

| Field | Meaning |
|---|---|
| **Tracked tags** | Comma-separated; these are excluded from results. Seeded from your default tags and previously fetched products. |
| **Min instance count** | Only suggest tags with at least this many questions instance-wide. |
| **Limit** | Max suggestions to return (1–200). |
| **Instance volume** | Total questions for that tag on the instance. |
| **Local count** | How many of that tag's questions you've already stored. |
| **Coverage** | `local_count / instance_count`, colour-coded: green ≥50%, amber ≥10%, grey below. |

**Low coverage on a high-volume untracked tag is the strongest "you're probably
missing signal here" flag** — that's the whole point of this tab.

This reads the cached tag index rather than calling SO directly. If it returns
nothing, the cache is probably empty: visit the Fetch page and browse or
validate a tag once to prime it.

---

## 12. Snoozed

Acknowledge a recurring `(product, main, sub)` pattern so it stops cluttering the
Dashboard's Key patterns, Top issues, and Recommended actions until the snooze
expires.

Snoozing only hides the pattern's *presentation*. The underlying questions and
classifications are untouched, and the pattern reappears automatically the moment
the snooze lapses.

**To snooze:** click **New snooze** and fill in product, main category, and
sub-category (all required — they must match the taxonomy strings in
[§4](#4-the-classification-taxonomy) exactly), plus optional **Days** and
**Reason**. Leave Days blank for an indefinite snooze.

**To un-snooze:** click **Restore** on the row and confirm.

Filter by product, and tick **Include expired** to see snoozes that have already
lapsed.

> There is currently no snooze button on the Dashboard itself — snoozes are
> created here. You'll need to copy the exact category strings from the pattern
> card you want to silence.

Snoozes are keyed by `(product, main, sub)`, so they survive window changes and
re-aggregation. The equivalent API calls:

```bash
# Snooze for 30 days
curl -X POST http://localhost:8000/api/patterns/dismiss \
  -H "Content-Type: application/json" \
  -d '{"product":"cloudsql","main":"Technical","sub":"Reliability issues or instability","days":30,"reason":"fix shipping in v1.4"}'

# List active snoozes for a product
curl 'http://localhost:8000/api/patterns/dismiss?product=cloudsql'

# Cancel
curl -X DELETE 'http://localhost:8000/api/patterns/dismiss?product=cloudsql&main=Technical&sub=Reliability%20issues%20or%20instability'

# Show snoozed items anyway in a summary call
curl 'http://localhost:8000/api/insights/summary?product=cloudsql&window=30&include_dismissed=true'
```

---

## 13. Run history

Past Fetch and Analysis runs, newest first: when each started, its status,
which products and window it covered, how long it took, and a counts summary.

| Column | Notes |
|---|---|
| **Status** | `done` (green), `partial` or `running` (amber), `failed` (red). `partial` means the run completed but hit per-item errors. |
| **Products / Window** | What the run covered. |
| **Duration** | Auto-formatted seconds → minutes → hours. Blank for a run that never finished. |
| **Counts** | Varies by run type — a fetch reports `inserted`, `skipped`, `errors`, `answers_fetched`; an aggregation reports `products` and `patterns`. |

Filter by status, and page 50 at a time. Use this to confirm a scheduled or
manual run actually completed before trusting the Dashboard or Metrics numbers
for that period.

---

## 14. Windows, date ranges, and the one gotcha

Every window control offers quick-picks (7 / 14 / 30 / 60 / 90 days) plus an
optional custom **from**/**to** range that overrides the quick-pick when set.
Each tab's window is independent.

Most numbers are computed live from whatever is in the database, so changing a
window on the Dashboard just re-filters and everything updates. **Key patterns
are the exception.**

> ### ⚠ Key patterns only appear when the Dashboard window matches the Analysis window
>
> Patterns are computed and *stored* during Analysis, tagged with the exact
> `window_days` value that run used. The Dashboard looks them up by that exact
> number.
>
> So if you ran Analysis at 30d and then view the Dashboard at 7d, **Key
> patterns and Recommended actions will be empty** — even though Signal
> questions, Category distribution, and Top issues all populate normally,
> because those are computed live.
>
> **Fix:** either set the Dashboard window back to the one you analysed with, or
> re-run Analysis at the window you want to view. The same applies to the
> Remediation guide, which is also stored per window.

A custom date range does not change this: the window quick-pick value is still
what patterns are looked up by, so pair a custom range with the same window
number you analysed at.

---

## 15. Exports

The Dashboard's **JSON / Markdown / PDF** buttons export a full snapshot of the
current product and window: every stat, the category breakdown, top issues,
patterns, recommended actions, every source question with its stored answers,
and the remediation guide if one has been generated.

Every question and answer referenced anywhere in an export carries its Stack
Overflow ID (`[Q#12345]` / `[A#67890]`), so any claim can be traced back to the
exact source post. Files download as `report_<product>_<window>d.<ext>`.

---

## 16. State persistence across tabs

Filters and last-loaded results survive switching tabs. You never have to
re-select a product, re-pick a window, or re-click Load just because you looked
at something else.

- **Fetch, Analysis, Remediation** — form inputs, progress logs, and the live
  progress stream for an in-flight run. A run keeps going while you're on
  another page, and survives a full browser reload by reconnecting to the
  still-running backend job.
- **Dashboard, Rising trends, Metrics, Tag suggestions, Scheduled auto-fetch** —
  filter inputs and the last successful response, mirrored to `sessionStorage`
  so a reload restores them too.
- **Settings connection form** — kept in memory only, including an unsaved API
  key, so a secret never touches browser storage. A reload clears the draft, not
  the saved server-side config.

Only an explicit action changes what's shown — changing a filter and reloading,
saving settings, or a reload where noted. Navigating the sidebar never does.

The **Snoozed** and **Run history** tabs reload from the server each visit, since
both are cheap and you want them current.

---

## 17. Performance expectations

| Operation | First run | Subsequent runs |
|---|---|---|
| Fetch (3 tags, 90d, ~16k questions) | **minutes** — see below | seconds–minutes (incremental) |
| Analysis (30 new questions, 8B model, CPU) | minutes | seconds if nothing new |
| Dashboard / Metrics / Trends / exports | instant | instant (reads the DB) |
| Remediation guide | ~1 model call per qualifying cluster | cached unless sources changed |

**Why a large first fetch used to take about an hour.** The dominant cost is
answer bodies — one extra API call per question that has answers — and those
used to be fetched strictly one at a time, so wall-clock time scaled linearly
with question count no matter how fast your instance responded.

Answer fetches now run with **bounded concurrency** (`ANSWER_FETCH_CONCURRENCY`,
default 10), so that same fetch completes in roughly `1 hour ÷ concurrency` — a
few minutes at the default. Raise it for a faster first fetch, or lower it if
your instance is shared or rate-limited. The daily call budget and per-call
retry/backoff apply unchanged either way.

---

## 18. Troubleshooting

| Symptom | Cause & fix |
|---|---|
| **Key patterns is empty but other Dashboard sections have data** | The Dashboard window doesn't match the window Analysis ran at. See [§14](#14-windows-date-ranges-and-the-one-gotcha). |
| **A tab is empty entirely** | Fetch hasn't run for that tag/window, or Fetch ran but Analysis hasn't classified the new questions. Check Metrics → Skipped / missing. |
| **401 Unauthorized** | Token invalid or rotated. Verify with `curl -H "Authorization: Bearer <key>" "<base>/tags?pageSize=100&page=1"`. |
| **Everything classified as noise, `/api/generate` 404** | The model name doesn't match `ollama list`. Pick an installed model in Settings. |
| **API key field looks empty after a reload** | Expected — the key is never echoed back. If Settings says "A key is currently saved", it's still configured server-side. |
| **Tags shown in red** | Not found on your instance — check spelling. Tags that merely couldn't be verified stay the normal colour. |
| **Tag dropdown says it can't reach Stack Overflow** | Check Settings → Test connection. Manual tag entry still works meanwhile. |
| **Tag suggestions returns nothing** | The cached tag index is empty — browse or validate a tag on the Fetch page to prime it. Or your threshold is too high, or you already track everything above it. |
| **Time to first answer shows `—`** | No answer bodies captured. Enable `FETCH_ANSWERS` and re-fetch. |
| **Remediation cards all say "not grounded"** | The model couldn't tie its output to real sources — usually because no answers were captured. Enable `FETCH_ANSWERS`, re-fetch, then Regenerate all. |
| **Sub-category chart / Top issues show fewer than expected** | Both cap at the top 8 by volume. If fewer appear, fewer than 8 distinct categories exist for that product and window. |
| **Incremental fetch isn't picking up edits** | By design — it only looks forward from the watermark. Turn incremental off to re-pull the range. |
| **`[Errno 2] No such file or directory` on startup** | A corporate `SSL_CERT_FILE` points at a missing file. The launchers handle this; for manual runs `unset SSL_CERT_FILE` first. |
| **On-prem instance with an internal CA** | Set `SO_CA_BUNDLE` to your PEM file. Certificates are still verified; this only adds a trusted issuer. |
| **UI shows old code after replacing files** | Re-run the launcher so it rebuilds the UI. |
