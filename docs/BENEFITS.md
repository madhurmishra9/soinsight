# SOInsight — Benefits of the Automation

This document explains, in detail, **why** SOInsight is built as an automated
pipeline rather than a set of manual steps, and the concrete benefits you get
from each layer of automation. It covers three things working together:

1. The **one-command launcher** (`start-windows.ps1` / `start-mac.sh`)
2. The **scheduled auto-fetch** that keeps data fresh on its own
3. The **incremental, idempotent pipeline** that makes every re-run cheap and safe

If you just want to run the tool, see the [README](../README.md) quick start and
the [User Guide](USER_GUIDE.md). This page is the *rationale* — read it to
understand the payoff.

---

## TL;DR

| Without automation | With SOInsight automation |
|---|---|
| Install Python, Node, deps, model, config, build, run servers — by hand, in order | One command does all of it; you edit one CONFIG block once |
| Remember to pull fresh questions every day | Scheduler refreshes on a cadence with no human in the loop |
| Re-fetching re-downloads everything and re-pays LLM cost | Incremental fetch + skip-already-classified: re-runs take **seconds** |
| A half-finished run leaves inconsistent state | Idempotent, dedup-by-`so_id`, single-flight scheduling — runs are safe to repeat |
| Question text and tokens leave your machine | Fully local: no hosted LLM, no telemetry, content never leaves the box |
| Two people get two different setups | Identical, reproducible environment from the same script |

---

## 1. One-command setup and run

The launcher (`start-windows.ps1` on Windows, `start-mac.sh` on macOS/Linux)
turns a multi-step, error-prone setup into a single command. With **no further
interaction**, it:

1. Creates a Python virtualenv (`.venv`) and installs backend dependencies.
2. Installs frontend dependencies (`npm install`).
3. Writes `backend/.env` from the CONFIG block (so settings persist).
4. Pulls the Ollama classification model if it is not already present.
5. Builds the UI and starts **one** server (the backend serves the built UI).
6. Enables the scheduled auto-fetch.
7. Opens **http://localhost:8000** in your browser.

### Why this matters

- **Lower barrier to entry.** A non-developer stakeholder can run an LLM-backed
  analytics stack without knowing what a virtualenv is, what `npm` does, or how
  to wire a frontend to a backend. They edit a labelled CONFIG block and run one
  line.
- **Reproducibility.** Everyone who runs the script gets the *same* environment —
  same dependencies, same model, same port, same wiring. "Works on my machine"
  stops being a class of bug, because there is only one machine setup.
- **Fewer setup errors.** The ordering (venv → deps → env → model → build → serve)
  is encoded once and executed the same way every time. You cannot forget a step,
  install in the wrong order, or start the frontend before the backend is healthy
  — the launcher waits on `/health` before it proceeds.
- **Self-healing for common gotchas.** The launcher proactively clears a stale
  `SSL_CERT_FILE` (a frequent corporate-laptop failure), creates the data
  directory, and falls back from `pip install -e ./backend` to
  `requirements.txt` if needed — so issues that would otherwise stop a manual run
  are handled for you.
- **Persistent config.** Because the script writes `backend/.env`, your API key,
  tags, and schedule survive restarts. You configure once and never re-enter the
  token.
- **One process to reason about.** In normal mode the backend also serves the
  built SPA, so there is a single server on one port — nothing to coordinate, one
  thing to stop (`Ctrl+C` / close the window).

### Two modes, one script

| Mode | Command | Use it for |
|---|---|---|
| **Normal** | `…start-windows.ps1` | Daily use — single server on `:8000`, built UI |
| **Developer** | `…start-windows.ps1 -Dev` | Hot reload — backend `:8000` + Vite `:5173` |

The same automation covers both the "just use it" and "work on it" paths, so you
never maintain two different setup procedures.

---

## 2. Scheduled auto-fetch — fresh data with nobody driving

The scheduler runs **inside the backend** as an asyncio background task and
re-runs the full pipeline (ingest → classify → aggregate) on a cadence. The
launcher enables it from your `DEFAULT_TAGS`, `SCHEDULE_INTERVAL_HOURS`, and
`SCHEDULE_WINDOW_DAYS`; you can also manage it from the UI or the API.

### Why this matters

- **Insights stay current without manual work.** Pain-point trends, new patterns,
  and category shifts are reflected automatically. You open the dashboard and the
  data is already up to date — no "did anyone run the fetch today?" ritual.
- **No extra infrastructure.** Because the scheduler lives in the backend process,
  there is no separate cron job, no Task Scheduler entry, no external worker to
  install, monitor, or keep in sync. Start the app and the schedule is live.
- **Safe overlap handling (single-flight).** A scheduled tick that fires while a
  run is still in progress is **skipped, not stacked**. Slow runs can never pile
  up into a backlog of overlapping jobs that thrash the LLM or the database.
- **Fully controllable.** Inspect and steer it without touching the launcher:

  ```bash
  # Enable a daily refresh of two tags over a 30-day window
  curl -X POST http://localhost:8000/api/schedule \
    -H "Content-Type: application/json" \
    -d '{"enabled": true, "interval_hours": 24, "products": ["cloudsql", "cloudstorage"], "window_days": 30}'

  # Inspect config and last/next run times
  curl http://localhost:8000/api/schedule/status

  # Fire a run immediately
  curl -X POST http://localhost:8000/api/schedule/trigger
  ```

---

## 3. Incremental + idempotent pipeline — cheap and safe to repeat

Automation is only useful if running it again is cheap and never corrupts state.
SOInsight's pipeline is built so that **re-running is always safe and almost
always fast.**

### Incremental by default

- **Fetch** downloads only questions newer than the last fetch per tag
  (`since = MAX(created_at)` in the DB), and stops paginating early once a page
  crosses that boundary. The first pull of three tags over 90 days might take
  ~1 hour; every pull after that takes **seconds to minutes**.
- **Analysis** pre-filters against the `classifications` table with a single
  query, so only *unclassified* questions ever reach the LLM. Already-classified
  questions are skipped — you never re-pay inference cost for work already done.
- **Dashboard / exports** read straight from SQLite, so viewing results is
  instant and needs no fetch or analysis at all.

| Operation | First run | Subsequent runs |
|---|---|---|
| Fetch (3 tags, 90d, ~16k questions) | ~1 hour | **seconds–minutes** (incremental) |
| Analysis (30 new questions, 8B model, CPU) | minutes | **seconds** if nothing new |
| Dashboard / exports | instant | instant (reads DB) |

### Idempotent by design

- Ingestion **dedupes on `so_id`** (upsert), so re-running a fetch never creates
  duplicate questions.
- Only unclassified questions hit the LLM; patterns are **upserted**, not
  appended.
- The result: you can re-run any stage, interrupt a run and start over, or let
  the schedule and a manual fetch overlap in intent — and the data stays
  consistent. There is no "clean up before you re-run" step.

### Why this matters

- **Speed compounds.** Because re-runs are incremental, frequent refreshes are
  practically free. That is what makes a daily (or hourly) schedule sensible
  rather than punishing.
- **Cost control.** The LLM is the expensive part of the pipeline. Skipping
  already-classified questions means inference cost scales with *new* questions,
  not total volume.
- **Resilience.** Interrupted runs, retries, and overlapping triggers don't leave
  half-written or duplicated state, so the automation can run unattended without
  someone watching for corruption.

---

## 4. Local-first by construction

The automation never trades convenience for exposure. The entire pipeline runs on
your machine:

- **No hosted LLM.** Classification and embeddings run on **local Ollama**.
  Question content is sent to `http://localhost:11434`, never to a third party.
- **No telemetry, no external writes.** The agent produces text recommendations
  only — it never writes to Confluence, Backstage, Jira, or ServiceNow. The only
  outbound network call is read-only HTTPS to *your* SO Enterprise instance.
- **Secrets stay on disk, git-ignored.** `backend/.env` (and any launcher holding
  a real key) are git-ignored; the key is never logged, never returned by an API
  response, and never embedded in exports.

So every benefit above — one-command setup, unattended scheduling, cheap re-runs —
is delivered **without** sending your data anywhere. Full details in
[docs/SECURITY.md](SECURITY.md).

---

## 5. Analyst-friction reducers

Three small features that cut the per-week friction once the pipeline is
running:

- **Snooze handled patterns.** Once a recurring `(product, main, sub)` cluster
  has been acknowledged or shipped a fix, snooze it for *N* days (or
  indefinitely) and it stops surfacing in Top Issues / patterns until the
  snooze expires. Keyed by `(product, main, sub)`, so it survives window
  changes and re-aggregation.
- **Rising-volume detector.** `/api/insights/trends` compares a recent window
  against the trailing baseline and flags categories whose recent volume is ≥
  `threshold`× the trailing average. Surfaces emerging issues you wouldn't
  spot from a static category breakdown alone.
- **Tag auto-discovery.** `/api/insights/tag-suggestions` surfaces tags from
  the SO instance that aren't in your tracked list yet, ranked by instance
  volume, with the local coverage ratio. Catches the case where a new product
  is getting traction and nobody added its tag to the schedule.

These are read-only and run against the same local SQLite — no extra cost
beyond the existing pipeline.

## 6. Who benefits, and how

| Role | What the automation gives them |
|---|---|
| **Platform / DevRel lead** | A standing, always-fresh view of recurring pain points per product, with zero daily upkeep |
| **Individual analyst** | Skip the setup yak-shave; spend time reading insights, not wiring tools |
| **Security / IT reviewer** | A fully local, auditable, no-telemetry posture that's the same on every machine |
| **New team member** | One command to a working environment — onboarding measured in minutes, not a setup doc they half-follow |

---

## See also

- [README](../README.md) — quick start and full feature reference
- [User Guide](USER_GUIDE.md) — first-time setup, daily use, the four pages, exports
- [Architecture](ARCHITECTURE.md) — components, data flow, scheduling, tables
- [Security](SECURITY.md) — data handling, secrets, network posture
