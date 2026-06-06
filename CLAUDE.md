# CLAUDE.md — SOInsight

> Project memory for Claude Code. Auto-loaded every session; survives compaction.
> This is a **behavioral contract**, not documentation. Every line changes how you act.
> Detailed references are imported below — read them before writing code they govern.

@docs/taxonomy.md
@docs/so-api.md
@EXECUTION_PLAN.md

---

## Mission

Ingest questions from the **internal LBG Stack Overflow (Enterprise, Premium, API v3)**, classify each into
**one main + one sub-category** from the fixed taxonomy, detect patterns **across users**, and produce **one
structured summary per product/tag** for product owners. Runs **fully local** (Ollama + ChromaDB + SQLite).

## Golden rules (never violate)

1. **Fixed taxonomy only.** Classifier outputs ONLY labels from `docs/taxonomy.md`. Never invent/rename/merge a label. Invalid output → retry, then route to `Misuse / Noise`.
2. **One main + one sub** per question. Single-label.
3. **Patterns, not individuals.** A cluster is a "pattern" only at **≥3 questions from ≥2 distinct users**. Recommendations come from patterns, never one question.
4. **Local inference only.** Ollama `llama3.1:8b` + `nomic-embed-text`. Never call a hosted LLM.
5. **Agent recommends, never writes** to Confluence/Backstage/Jira/ServiceNow. Suggested actions are text only.
6. **Internal SO only.** Never query public Stack Overflow / Stack Exchange.
7. **Secrets via env only.** `SO_BASE_URL`, `SO_API_KEY`, `SO_TEAM`, `OLLAMA_URL` from `.env`. Never hardcode. Never log the key or full question bodies.
8. **Verify the API against Swagger** (`<SO_BASE_URL>/api/v3`) before coding param names/fields. Do NOT assume public-API shapes.
9. **Build in the order in `EXECUTION_PLAN.md`.** One session = one phase. Do not scaffold later phases early.

## Stack (pinned)

- **Backend (Py 3.11):** fastapi · uvicorn · pydantic v2 + pydantic-settings · sqlmodel · httpx · tenacity · chromadb · ollama · structlog. Dev: pytest · pytest-asyncio · ruff · mypy.
- **Frontend:** react 18 + vite · react-router-dom · axios · recharts · lucide-react.

## Architecture

```
React (Vite) ─REST+SSE─> FastAPI ─> SO Adapter (Enterprise v3, api_key) ─> internal SO
                            ├─> embeddings ─> Ollama (nomic-embed-text)
                            ├─> ChromaDB (dedup + clustering)
                            ├─> classifier ─> Ollama (llama3.1:8b, enum-constrained)
                            ├─> aggregator (patterns, trends, recommendation matrix)
                            └─> SQLite (raw + classification + analysis)
```

## Conventions (definition of "production-ready")

- Config via `pydantic-settings` + `.env` (+ `.env.example`). Never raw `os.environ`.
- `structlog` JSON logs; one event per significant step; never log secrets/PII.
- Every SO + Ollama call wrapped in `tenacity` retry (exp backoff, capped). No silent excepts.
- Async client + handlers; long jobs run as background tasks streaming via SSE.
- **Idempotent** ingestion + classification, keyed by `so_id`. No duplicate rows/embeddings.
- Full type hints; `mypy` + `ruff` clean. Tests for: taxonomy validator, classifier contract (mock Ollama), aggregator thresholds, SO paging (mock httpx).
- `GET /health` (process) + `GET /health/deps` (Ollama + SO reachability).
- Multi-stage Dockerfile, non-root, healthcheck. `docker-compose up` runs the whole stack.

## Out of scope (do not build)

External SO · writes to other systems · Teams/codebase/support-ticket sources · real-time push · multi-label · hosted-LLM calls.

## Data model (SQLite via SQLModel)

`questions`(so_id, title, body, tags, score, view_count, created_at, author_id, author_role, answer_count, has_accepted, team_slug) ·
`classifications`(question_id, main_category, sub_category, confidence, is_noise, model, classified_at) ·
`patterns`(product_tag, window_days, main_category, sub_category, question_count, distinct_users, summary, suggested_action, first_seen, last_seen) ·
`runs`(started_at, finished_at, products, window_days, status, counts)

---

<!-- Tip: as the repo grows, move path-specific rules into .claude/rules/*.md with `paths:` globs
     so they load only when matching files are edited (keeps this file lean and high-signal). -->
