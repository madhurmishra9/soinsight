# Stack Overflow Enterprise v3 — API Contract

> Imported by CLAUDE.md. Read before writing `services/so_client.py`.
> **Verify exact param names + response fields against your instance Swagger** at `<SO_BASE_URL>/api/v3`
> before coding. v3 and v2.3 differ; do not assume public-SO-API shapes.

## Instance

- **Base URL:** `https://<site>.stackenterprise.co/api/v3`  → env `SO_BASE_URL`
- **Tier:** Premium Enterprise, read access. Budget **10,000 calls/day** — ample. Throttle is a safety net, not a gate.
- **Auth:** **api_key** (chosen). Always send a `User-Agent` header or requests get throttled.

## Auth abstraction (swappable)

```python
# services/so_client.py
class SOAuth:
    """Current mode: api_key. Kept swappable for bearer/PAT without touching call sites."""
    def __init__(self, mode: str, api_key: str | None = None, access_token: str | None = None):
        self.mode = mode  # "api_key" | "bearer" | "key+token"
        self.api_key = api_key
        self.access_token = access_token

    def headers(self) -> dict[str, str]:
        h = {"User-Agent": "soinsight/1.0"}            # REQUIRED
        if self.mode in ("bearer", "key+token") and self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        if self.mode in ("api_key", "key+token") and self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    def params(self) -> dict[str, str]:
        # Only if your Swagger requires the key as a query param instead of/in addition to the header.
        return {}
```

## Client requirements

- **Async** (`httpx.AsyncClient`), shared client, sane timeouts.
- **Retry** every call with `tenacity` (exponential backoff, capped, retry on 429/5xx/timeout).
- **Pagination:** request `pageSize=100`; loop pages until exhausted. (Confirm `page`/`pageSize` vs `pagesize` in Swagger.)
- **Time window:** filter questions by creation date for 30/60/90-day windows. **Confirm the exact date param name in
  Swagger** — do not assume `fromdate`.
- **Rate guard:** detect/track daily budget; token-bucket throttle. At 10k/day this won't gate normal runs.
- **Idempotent:** dedup by `so_id`; safe to re-run.

## Endpoints used

| Endpoint        | Purpose                                  | Phase |
|-----------------|------------------------------------------|-------|
| `/questions`    | Core ingestion — tagged Qs in a window   | S2    |
| `/tags`         | Product/tag list + post counts           | S2    |
| `/users/{id}`   | Author info (technical-vs-not heuristic) | S5    |

## Private Teams / Communities (discovery — S1)

If the instance segments products into **Private Teams** or **Communities**, a single main-site call misses siloed
content. At connection time, enumerate available teams/communities and let the user select which to include; the client
must query each selected scope separately and tag rows with `team_slug`.

## Field mapping (confirm against Swagger, then freeze in a Pydantic model)

`so_id, title, body, tags[], score, view_count, created_at, owner/author{id, role?}, answer_count, is_answered/has_accepted`.
Map missing/renamed fields explicitly; never silently drop a field the aggregator needs.
