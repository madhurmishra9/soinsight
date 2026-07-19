"""
Tests for services/so_client.py.
All HTTP calls are mocked via httpx.MockTransport — no real network required.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from services.so_client import _PAGE_PARAM, _PAGE_SIZE_PARAM, SOAuth, SOClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(
    payload: object,
    status_code: int = 200,
) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )


def _mock_transport(*responses: httpx.Response) -> httpx.MockTransport:
    """Return responses in order; repeat the last one if more calls are made."""
    resp_list = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        r = resp_list.pop(0) if len(resp_list) > 1 else resp_list[0]
        # Attach the triggering request so raise_for_status() works properly.
        r.request = request
        return r

    return httpx.MockTransport(handler)


async def _client_with(transport: httpx.MockTransport) -> SOClient:
    auth = SOAuth(mode="api_key", api_key="test-secret-key")
    client = SOClient(base_url="https://so.example.com/api/v3", auth=auth)
    client._client = httpx.AsyncClient(
        base_url="https://so.example.com/api/v3",
        headers=auth.headers(),
        transport=transport,
    )
    return client


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Stub out _paginate's inter-page throttle so tests stay fast/deterministic;
    exposes recorded delays for tests that want to assert on the throttle itself."""
    calls: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr("services.so_client.asyncio.sleep", _fake_sleep)
    return calls


# ---------------------------------------------------------------------------
# Auth header tests
# ---------------------------------------------------------------------------

def test_auth_headers_contain_user_agent() -> None:
    auth = SOAuth(mode="api_key", api_key="mykey")
    assert auth.headers()["User-Agent"] == "soinsight/1.0"


def test_auth_headers_contain_api_key() -> None:
    auth = SOAuth(mode="api_key", api_key="secret")
    assert auth.headers()["X-API-Key"] == "secret"


def test_auth_headers_no_bearer_in_api_key_mode() -> None:
    auth = SOAuth(mode="api_key", api_key="secret")
    assert "Authorization" not in auth.headers()


def test_auth_bearer_mode() -> None:
    auth = SOAuth(mode="bearer", access_token="tok")
    h = auth.headers()
    assert h["Authorization"] == "Bearer tok"
    assert "X-API-Key" not in h


def test_auth_key_plus_token_mode() -> None:
    auth = SOAuth(mode="key+token", api_key="k", access_token="t")
    h = auth.headers()
    assert "X-API-Key" in h
    assert "Authorization" in h


# ---------------------------------------------------------------------------
# Key never logged
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_key_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    transport = _mock_transport(_make_response({"items": [], "has_more": False}))
    client = await _client_with(transport)

    import logging
    with caplog.at_level(logging.DEBUG):
        await client._get("/tags")

    for record in caplog.records:
        assert "test-secret-key" not in record.getMessage()
        assert "test-secret-key" not in str(record.__dict__)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pagination_fetches_all_pages() -> None:
    page1 = {"items": [{"id": i} for i in range(100)], "has_more": True}
    page2 = {"items": [{"id": i} for i in range(100, 150)], "has_more": False}

    transport = _mock_transport(
        _make_response(page1),
        _make_response(page2),
    )
    client = await _client_with(transport)

    collected: list[object] = []
    async for page in client._paginate("/questions"):
        collected.extend(page)

    assert len(collected) == 150


@pytest.mark.asyncio
async def test_pagination_stops_on_empty_page() -> None:
    transport = _mock_transport(_make_response({"items": [], "has_more": False}))
    client = await _client_with(transport)

    pages: list[object] = []
    async for page in client._paginate("/tags"):
        pages.extend(page)

    assert pages == []


@pytest.mark.asyncio
async def test_pagination_sends_page_and_pagesize_params() -> None:
    seen_params: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        r = _make_response({"items": [], "has_more": False})
        r.request = request
        return r

    transport = httpx.MockTransport(handler)
    client = await _client_with(transport)

    async for _ in client._paginate("/questions"):
        pass

    assert len(seen_params) >= 1
    first = seen_params[0]
    assert _PAGE_PARAM in first
    assert _PAGE_SIZE_PARAM in first
    assert first[_PAGE_PARAM] == "1"
    assert first[_PAGE_SIZE_PARAM] == "100"


# ---------------------------------------------------------------------------
# Retry on 429 / 5xx
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    """Client retries on 429 and eventually succeeds."""
    monkeypatch.setattr("services.so_client._RETRY_WAIT_MIN", 0)
    monkeypatch.setattr("services.so_client._RETRY_WAIT_MAX", 0)

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        status = 429 if call_count < 3 else 200
        r = _make_response({"items": [], "has_more": False}, status_code=status)
        r.request = request
        return r

    transport = httpx.MockTransport(handler)
    client = await _client_with(transport)
    await client._get("/tags")

    assert call_count == 3


@pytest.mark.asyncio
async def test_retry_on_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("services.so_client._RETRY_WAIT_MIN", 0)
    monkeypatch.setattr("services.so_client._RETRY_WAIT_MAX", 0)

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        status = 500 if call_count < 2 else 200
        r = _make_response({"items": []}, status_code=status)
        r.request = request
        return r

    transport = httpx.MockTransport(handler)
    client = await _client_with(transport)
    await client._get("/tags")

    assert call_count == 2


# ---------------------------------------------------------------------------
# list_scopes — 404 is not an error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_scopes_returns_empty_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        r = _make_response({"error": "not found"}, status_code=404)
        r.request = request
        return r

    transport = httpx.MockTransport(handler)
    client = await _client_with(transport)
    scopes = await client.list_scopes()
    assert scopes == []


@pytest.mark.asyncio
async def test_list_scopes_returns_slugs() -> None:
    transport = _mock_transport(
        _make_response({
            "items": [{"slug": "team-a", "name": "Team A"}, {"slug": "team-b", "name": "Team B"}],
            "has_more": False,
        })
    )
    client = await _client_with(transport)
    scopes = await client.list_scopes()
    slugs = [s["slug"] for s in scopes]
    assert "team-a" in slugs
    assert "team-b" in slugs


# ---------------------------------------------------------------------------
# iter_questions — date param is sent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_iter_questions_sends_date_param() -> None:
    from services.so_client import _DATE_FROM_PARAM

    seen_params: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        r = _make_response({"items": [], "has_more": False})
        r.request = request
        return r

    transport = httpx.MockTransport(handler)
    client = await _client_with(transport)
    since = datetime(2024, 1, 1, tzinfo=UTC)

    async for _ in client.iter_questions(tag="python", since=since):
        pass

    assert len(seen_params) >= 1
    assert _DATE_FROM_PARAM in seen_params[0]


@pytest.mark.asyncio
async def test_iter_questions_yields_items() -> None:
    transport = _mock_transport(
        _make_response(
            {"items": [{"so_id": 1, "title": "Q1"}, {"so_id": 2, "title": "Q2"}], "has_more": False}
        )
    )
    client = await _client_with(transport)
    since = datetime(2024, 1, 1, tzinfo=UTC)

    items = [q async for q in client.iter_questions(tag="python", since=since)]
    assert len(items) == 2
    assert items[0]["title"] == "Q1"


# ---------------------------------------------------------------------------
# Retry exhaustion and non-retryable errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_exhausted_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """After _RETRY_ATTEMPTS all returning 429, the exception propagates."""
    monkeypatch.setattr("services.so_client._RETRY_ATTEMPTS", 2)
    monkeypatch.setattr("services.so_client._RETRY_WAIT_MIN", 0)
    monkeypatch.setattr("services.so_client._RETRY_WAIT_MAX", 0)
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        r = _make_response({}, status_code=429)
        r.request = request
        return r

    transport = httpx.MockTransport(handler)
    client = await _client_with(transport)

    with pytest.raises(httpx.HTTPStatusError):
        await client._get("/tags")

    assert call_count == 2  # exactly _RETRY_ATTEMPTS attempts made


@pytest.mark.asyncio
async def test_non_retryable_401_raises_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """401 Unauthorized is not in the retry list — should fail on the first attempt."""
    monkeypatch.setattr("services.so_client._RETRY_WAIT_MIN", 0)
    monkeypatch.setattr("services.so_client._RETRY_WAIT_MAX", 0)
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        r = _make_response({}, status_code=401)
        r.request = request
        return r

    transport = httpx.MockTransport(handler)
    client = await _client_with(transport)

    with pytest.raises(httpx.HTTPStatusError):
        await client._get("/tags")

    assert call_count == 1  # no retries for non-retryable status


@pytest.mark.asyncio
async def test_retry_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """httpx.ReadTimeout is retried; client eventually succeeds on the third attempt."""
    monkeypatch.setattr("services.so_client._RETRY_WAIT_MIN", 0)
    monkeypatch.setattr("services.so_client._RETRY_WAIT_MAX", 0)
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise httpx.ReadTimeout("mock timeout", request=request)
        r = _make_response({"items": [], "has_more": False})
        r.request = request
        return r

    transport = httpx.MockTransport(handler)
    client = await _client_with(transport)
    await client._get("/tags")

    assert call_count == 3


# ---------------------------------------------------------------------------
# test_connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_connection_success() -> None:
    """When /info returns a version dict, test_connection reports ok=True."""
    transport = _mock_transport(_make_response({"version": "3.1.0", "status": "ok"}))
    client = await _client_with(transport)
    result = await client.test_connection()
    assert result["ok"] is True
    assert result["version"] == "3.1.0"


@pytest.mark.asyncio
async def test_test_connection_fallback_to_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    """/info 404 → falls back to /tags; ok=True with version='unknown'."""
    monkeypatch.setattr("services.so_client._RETRY_WAIT_MIN", 0)
    monkeypatch.setattr("services.so_client._RETRY_WAIT_MAX", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        if "/info" in str(request.url.path):
            r = _make_response({"error": "not found"}, status_code=404)
        else:
            r = _make_response({"items": [], "has_more": False})
        r.request = request
        return r

    transport = httpx.MockTransport(handler)
    client = await _client_with(transport)
    result = await client.test_connection()
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_test_connection_total_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both /info and /tags fail → ok=False with error key present."""
    monkeypatch.setattr("services.so_client._RETRY_ATTEMPTS", 1)
    monkeypatch.setattr("services.so_client._RETRY_WAIT_MIN", 0)
    monkeypatch.setattr("services.so_client._RETRY_WAIT_MAX", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        r = _make_response({}, status_code=500)
        r.request = request
        return r

    transport = httpx.MockTransport(handler)
    client = await _client_with(transport)
    result = await client.test_connection()
    assert result["ok"] is False
    assert "error" in result


# ---------------------------------------------------------------------------
# list_tags + team-scoped paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tags_yields_items() -> None:
    transport = _mock_transport(
        _make_response({
            "items": [{"name": "python", "count": 42}, {"name": "java", "count": 10}],
            "has_more": False,
        })
    )
    client = await _client_with(transport)
    items = [t async for t in client.list_tags()]
    assert len(items) == 2
    assert items[0]["name"] == "python"


@pytest.mark.asyncio
async def test_iter_questions_team_scoped_uses_team_path() -> None:
    """When team= is supplied, the request path must include /teams/{team}/."""
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(str(request.url.path))
        r = _make_response({"items": [], "has_more": False})
        r.request = request
        return r

    transport = httpx.MockTransport(handler)
    client = await _client_with(transport)
    since = datetime(2024, 1, 1, tzinfo=UTC)

    async for _ in client.iter_questions(tag="python", since=since, team="my-team"):
        pass

    assert any("/teams/my-team/" in p for p in seen_paths)


@pytest.mark.asyncio
async def test_paginate_sleeps_between_pages(_no_real_sleep: list[float]) -> None:
    """A 0.3s throttle runs between pages of a multi-page fetch (rate-limit
    courtesy to the SO instance), but not after the final page."""
    page1 = {"items": [{"id": i} for i in range(100)], "has_more": True}
    page2 = {"items": [{"id": i} for i in range(100, 150)], "has_more": False}

    transport = _mock_transport(_make_response(page1), _make_response(page2))
    client = await _client_with(transport)

    async for _ in client._paginate("/questions"):
        pass

    assert _no_real_sleep == [0.3]


@pytest.mark.asyncio
async def test_paginate_no_sleep_after_single_page(_no_real_sleep: list[float]) -> None:
    transport = _mock_transport(_make_response({"items": [], "has_more": False}))
    client = await _client_with(transport)

    async for _ in client._paginate("/tags"):
        pass

    assert _no_real_sleep == []


@pytest.mark.asyncio
async def test_paginate_raw_list_response() -> None:
    """_paginate handles an API that returns a plain list (not wrapped in {items:[]})."""
    transport = _mock_transport(_make_response([{"id": 1}, {"id": 2}, {"id": 3}]))
    client = await _client_with(transport)

    items: list[object] = []
    async for page in client._paginate("/questions"):
        items.extend(page)

    assert len(items) == 3


# ---------------------------------------------------------------------------
# TLS verification (__aenter__ wiring to app settings)
# ---------------------------------------------------------------------------

class _FakeAsyncClient:
    """Captures the kwargs SOClient.__aenter__ passes to httpx.AsyncClient."""

    last_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        _FakeAsyncClient.last_kwargs = kwargs

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_aenter_defaults_to_verify_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """No CA bundle configured -> normal cert verification (verify=True), not disabled."""
    monkeypatch.setattr("services.so_client.settings.so_ca_bundle", "")
    monkeypatch.setattr("services.so_client.httpx.AsyncClient", _FakeAsyncClient)

    auth = SOAuth(mode="api_key", api_key="k")
    async with SOClient(base_url="https://so.example.com/api/v3", auth=auth):
        pass

    assert _FakeAsyncClient.last_kwargs["verify"] is True


@pytest.mark.asyncio
async def test_aenter_uses_configured_ca_bundle_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured CA bundle path is passed through as verify=<path> -- adds a
    trusted issuer, never disables verification outright."""
    monkeypatch.setattr(
        "services.so_client.settings.so_ca_bundle", "/etc/ssl/certs/internal-ca.pem"
    )
    monkeypatch.setattr("services.so_client.httpx.AsyncClient", _FakeAsyncClient)

    auth = SOAuth(mode="api_key", api_key="k")
    async with SOClient(base_url="https://so.example.com/api/v3", auth=auth):
        pass

    assert _FakeAsyncClient.last_kwargs["verify"] == "/etc/ssl/certs/internal-ca.pem"


@pytest.mark.asyncio
async def test_aenter_insecure_skip_verify_disables_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The opt-in escape hatch, when explicitly enabled, disables cert checks."""
    monkeypatch.setattr("services.so_client.settings.so_insecure_skip_verify", True)
    monkeypatch.setattr("services.so_client.httpx.AsyncClient", _FakeAsyncClient)

    auth = SOAuth(mode="api_key", api_key="k")
    async with SOClient(base_url="https://so.example.com/api/v3", auth=auth):
        pass

    assert _FakeAsyncClient.last_kwargs["verify"] is False


@pytest.mark.asyncio
async def test_aenter_insecure_skip_verify_overrides_ca_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """so_insecure_skip_verify takes priority when both it and a CA bundle are set."""
    monkeypatch.setattr("services.so_client.settings.so_insecure_skip_verify", True)
    monkeypatch.setattr(
        "services.so_client.settings.so_ca_bundle", "/etc/ssl/certs/internal-ca.pem"
    )
    monkeypatch.setattr("services.so_client.httpx.AsyncClient", _FakeAsyncClient)

    auth = SOAuth(mode="api_key", api_key="k")
    async with SOClient(base_url="https://so.example.com/api/v3", auth=auth):
        pass

    assert _FakeAsyncClient.last_kwargs["verify"] is False


@pytest.mark.asyncio
async def test_aenter_returns_self() -> None:
    auth = SOAuth(mode="api_key", api_key="k")
    client = SOClient(base_url="https://so.example.com/api/v3", auth=auth)
    async with client as entered:
        assert entered is client
