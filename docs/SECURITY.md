# SOInsight — Security & Compliance Notes

## Scope
SOInsight is a **local, single-user** analysis tool. It binds to
`127.0.0.1` only and is not designed for multi-user or internet-facing
deployment.

## Data handling
- **All data stays local.** Questions are stored in SQLite on disk; the only
  outbound calls are to your SO Enterprise instance (HTTPS, bearer) and the
  local Ollama daemon. No telemetry, no third-party services, no cloud LLM —
  question content never leaves the machine.
- The classification LLM runs entirely on-device via Ollama.

## Secrets
- The SO API key is held in `backend/.env` (file permissions of the local
  user) and in process memory. It is **never** logged, never returned by any
  API response (`SOConfigResponse` deliberately omits it; `SecretStr` is used
  on input), and never embedded in exports.
- `backend/.env`, `backend/data/`, and the launchers (if they contain a real
  key) are excluded from git via `.gitignore`. Rotate the token via your SO
  Enterprise account; update `.env` or Settings.

## Network posture
- Server binds `127.0.0.1:8000` (not reachable from the network).
- CORS allows only `localhost:5173` / `localhost:8000` origins.
- Security headers on every response: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Permissions-Policy` (camera/mic/geolocation denied).
- Outbound TLS uses certifi; a broken corporate `SSL_CERT_FILE` is
  self-healed at startup rather than disabling verification.

## Static serving (prod-mode SPA)
The SPA catch-all in `app/main.py` resolves every requested path under
`frontend/dist` and verifies the resolved location stays inside that
directory. Requests like `GET /../backend/.env` (or URL-encoded variants
of `..`) are refused and fall back to `index.html`. The check is
covered by `tests/test_main.py` so any future refactor that re-opens
the traversal hole fails CI.

## Long-running stream hygiene
SSE streams (`/api/questions/stream`, `/api/remediation/stream`) wrap
their event-generator in a `try / finally` that removes the run's queue
from the in-process registry. This prevents unbounded `_run_queues`
growth across long uptimes and disconnects (tested in
`tests/test_run_queue_cleanup.py`).

## Auth to Stack Overflow Enterprise
- Bearer-token only (`Authorization: Bearer <token>`); read-only API usage
  (questions/tags). The tool never writes to SO, Confluence, Backstage, or
  Jira — recommendations are advisory output.

## Dependency hygiene
- Pinned via `backend/pyproject.toml` and `frontend/package-lock.json`.
- Suggested checks in CI or locally: `pip-audit` (Python) and `npm audit`
  (frontend).

## Known limitations (by design)
- No user authentication on the local UI (single-user, loopback-only).
- SQLite is unencrypted at rest; rely on OS-level disk encryption
  (FileVault/BitLocker) per your endpoint policy.
- The technical/non-technical split is a tag heuristic, clearly labelled
  APPROXIMATE in UI and exports — never a verified user attribute.
