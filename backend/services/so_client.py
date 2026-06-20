"""
Stack Overflow Enterprise v3 client.

IMPORTANT — Swagger verification required before first real run:
  Open <SO_BASE_URL>/api/v3 in a browser and confirm:
    - _PAGE_PARAM      : pagination page param name  (guessed: "page")
    - _PAGE_SIZE_PARAM : page-size param name        (guessed: "pageSize")
    - _DATE_FROM_PARAM : earliest-date filter param  (guessed: "fromdate")
  Update the constants below once confirmed. Wrong param names silently return
  all results without date/page filtering, causing over-fetching.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx
import structlog
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.settings import settings

log = structlog.get_logger("soinsight.so_client")

# ---------------------------------------------------------------------------
# TODO: confirm these three param names against your instance Swagger at
#       <SO_BASE_URL>/api/v3  before running against a real instance.
# ---------------------------------------------------------------------------
_PAGE_PARAM = "page"          # guessed — verify in Swagger
_PAGE_SIZE_PARAM = "pageSize"  # guessed — verify in Swagger
_DATE_FROM_PARAM = "fromdate"
_DATE_TO_PARAM = "todate"
# ---------------------------------------------------------------------------

_PAGE_SIZE = 100
_RETRY_ATTEMPTS = 4
_RETRY_WAIT_MIN = 1    # seconds
_RETRY_WAIT_MAX = 30   # seconds


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False



class SOAuth:
    """
    Auth abstraction — current mode: api_key.
    Kept swappable for bearer/PAT without touching call sites.
    """

    def __init__(
        self,
        mode: str = "api_key",
        api_key: str | None = None,
        access_token: str | None = None,
    ) -> None:
        self.mode = mode
        self._api_key = api_key
        self._access_token = access_token

    def headers(self) -> dict[str, str]:
        h: dict[str, str] = {"User-Agent": "soinsight/1.0"}
        if self.mode in ("bearer", "key+token"):
            token = self._access_token or self._api_key
            if token:
                h["Authorization"] = f"Bearer {token}"
        if self.mode in ("api_key", "key+token") and self._api_key:
            h["X-API-Key"] = self._api_key
        return h

    def params(self) -> dict[str, str]:
        # Only needed if Swagger requires the key as a query param.
        # Leave empty for header-only auth (the default).
        return {}


class SOClient:
    """Async, retrying, paginating client for SO Enterprise v3."""

    def __init__(self, base_url: str, auth: SOAuth, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = auth
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> SOClient:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._auth.headers(),
            params=self._auth.params(),
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("SOClient must be used as an async context manager")
        return self._client

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        # AsyncRetrying reads _RETRY_WAIT_* at call time — monkeypatching works correctly.
        url = path if path.startswith("/") else f"/{path}"
        log.debug("so_get", path=url)
        result: Any = None
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(_RETRY_ATTEMPTS),
            wait=wait_exponential(multiplier=1, min=_RETRY_WAIT_MIN, max=_RETRY_WAIT_MAX),
            reraise=True,
        ):
            with attempt:
                r = await self._http.get(url, params=params or {})
                r.raise_for_status()
                result = r.json()
        return result

    async def _paginate(
        self, path: str, params: dict[str, Any] | None = None
    ) -> AsyncIterator[list[Any]]:
        """Yield one page of items at a time until exhausted."""
        base_params: dict[str, Any] = {_PAGE_SIZE_PARAM: _PAGE_SIZE, **(params or {})}
        page = 1
        while True:
            base_params[_PAGE_PARAM] = page
            data = await self._get(path, base_params)

            # v3 responses may be a list directly, or wrapped in {"items": [...]}
            if isinstance(data, list):
                items: list[Any] = data
                has_more = len(items) == _PAGE_SIZE
            else:
                items = data.get("items", [])
                has_more = data.get("has_more", len(items) == _PAGE_SIZE)

            if not items:
                break

            yield items

            if not has_more:
                break
            page += 1

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def test_connection(self) -> dict[str, Any]:
        """
        Probe the instance. Returns {"version": str, "ok": bool}.
        Hits the API info endpoint; falls back gracefully if not present.
        """
        try:
            # TODO: confirm the info/version endpoint path in Swagger.
            # Guessing /info — adjust if your instance differs.
            data = await self._get("/info")
            version = data.get("version", "unknown") if isinstance(data, dict) else "unknown"
        except Exception:
            # Fallback: any successful paginated call proves connectivity.
            try:
                await self._get("/tags", {_PAGE_SIZE_PARAM: 15, _PAGE_PARAM: 1})
                version = "unknown"
            except Exception as exc:
                log.warning("so_connection_failed", error=str(exc))
                return {"ok": False, "version": None, "error": str(exc)}

        log.info("so_connection_ok", version=version)
        return {"ok": True, "version": version}

    async def list_scopes(self) -> list[dict[str, Any]]:
        """
        Discover Private Teams / Communities available on this instance.
        Returns a list of scope dicts: [{"slug": str, "name": str, ...}].

        TODO: confirm the teams/communities endpoint path in Swagger.
        Guessing /teams — adjust if your instance uses /communities or similar.
        """
        scopes: list[dict[str, Any]] = []
        try:
            async for page in self._paginate("/teams"):
                for item in page:
                    if isinstance(item, dict):
                        scopes.append(
                            {
                                "slug": item.get("slug", item.get("id", "")),
                                "name": item.get("name", ""),
                                "type": "team",
                            }
                        )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # Instance may not support teams — not an error.
                log.info("so_teams_not_supported", status=404)
            else:
                raise
        return scopes

    async def iter_questions(
        self,
        tag: str,
        since: datetime,
        until: datetime | None = None,
        team: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Yield individual question dicts for *tag* created on or after *since*.
        If *team* is provided, scopes the request to that team/community.

        TODO: confirm _DATE_FROM_PARAM name and team scoping mechanism in Swagger.
        Some instances scope via a query param; others via a URL prefix.
        """
        params: dict[str, Any] = {
            "tags": tag,
            _DATE_FROM_PARAM: int(since.timestamp()),
        }
        if until is not None:
            params[_DATE_TO_PARAM] = int(until.timestamp())

        # TODO: confirm team-scoping approach. Options:
        #   (a) query param: params["team"] = team
        #   (b) URL prefix: path = f"/teams/{team}/questions"
        # Using option (b) as the primary guess; falls back to main /questions.
        path = f"/teams/{team}/questions" if team else "/questions"

        async for page in self._paginate(path, params):
            exhausted = False
            for item in page:
                if not isinstance(item, dict):
                    continue
                raw_date = item.get("creationDate") or item.get("creation_date")
                if raw_date:
                    try:
                        q_dt = datetime.fromisoformat(
                            str(raw_date).replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                        if q_dt < since:
                            exhausted = True
                            break
                        if until is not None and q_dt > until:
                            continue
                    except (ValueError, TypeError):
                        pass
                yield item
            if exhausted:
                return

    async def iter_answers(
        self,
        question_id: int,
        team: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Yield individual answer dicts for the question with id *question_id*.
        Scoped to *team* if provided.

        TODO: confirm the answers path and team-scoping in Swagger. SO Enterprise
        v3 typically exposes answers at /questions/{id}/answers; some instances
        require a body/filter param to include the answer body. If the body comes
        back empty, add the appropriate include/filter param here.
        """
        path = (
            f"/teams/{team}/questions/{question_id}/answers"
            if team
            else f"/questions/{question_id}/answers"
        )
        try:
            async for page in self._paginate(path):
                for item in page:
                    if isinstance(item, dict):
                        yield item
        except httpx.HTTPStatusError as exc:
            # A question with no answers (or an instance that 404s the sub-resource)
            # is not a fatal error — just yields nothing.
            if exc.response.status_code == 404:
                log.info("so_answers_not_found", question_id=question_id)
                return
            raise

    async def list_tags(self, team: str | None = None) -> AsyncIterator[dict[str, Any]]:
        """
        Yield tag dicts (name, question_count, …).
        Scoped to *team* if provided.

        TODO: confirm team-scoped tags path in Swagger.
        """
        path = f"/teams/{team}/tags" if team else "/tags"
        async for page in self._paginate(path):
            for item in page:
                if isinstance(item, dict):
                    yield item


# ---------------------------------------------------------------------------
# Module-level factory — builds a client from app settings
# ---------------------------------------------------------------------------

def make_client() -> SOClient:
    auth = SOAuth(mode="api_key", api_key=settings.so_api_key or None)
    return SOClient(base_url=settings.so_base_url, auth=auth)
