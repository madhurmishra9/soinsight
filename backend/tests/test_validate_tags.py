"""
Tests for GET /api/questions/validate-tags.

The SO client is faked so no network is hit. Key guarantee: when the tag list
cannot be fetched, tags come back "unknown" (never "unavailable"), so a valid
tag is never wrongly flagged red.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.questions as rq
from routers.questions import router as questions_router


class _FakeClient:
    def __init__(
        self, tags: list[dict[str, Any]] | None = None, raise_on_list: bool = False
    ) -> None:
        self._tags = tags or []
        self._raise = raise_on_list

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False

    async def list_tags(self, team: str | None = None) -> AsyncIterator[dict[str, Any]]:
        if self._raise:
            raise RuntimeError("SO unreachable")
        for t in self._tags:
            yield t


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(questions_router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    rq._tag_index_cache.clear()
    yield
    rq._tag_index_cache.clear()


def test_validate_tags_available_and_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    tags = [{"name": "python", "questionCount": 10}, {"name": "docker"}]
    monkeypatch.setattr(rq, "SOClient", lambda **_kw: _FakeClient(tags=tags))

    r = _client().get("/api/questions/validate-tags", params={"tags": "python,Docker,nonexistent"})
    by_tag = {v["tag"]: v for v in r.json()}
    assert by_tag["python"]["status"] == "available"
    assert by_tag["python"]["question_count"] == 10
    assert by_tag["Docker"]["status"] == "available"   # case-insensitive match
    assert by_tag["nonexistent"]["status"] == "unavailable"


def test_validate_tags_unknown_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rq, "SOClient", lambda **_kw: _FakeClient(raise_on_list=True))

    r = _client().get("/api/questions/validate-tags", params={"tags": "python,whatever"})
    statuses = {v["tag"]: v["status"] for v in r.json()}
    assert statuses == {"python": "unknown", "whatever": "unknown"}


def test_validate_tags_empty_returns_empty() -> None:
    assert _client().get("/api/questions/validate-tags", params={"tags": ""}).json() == []


def _warm_cache(tags: dict[str, int], team: str | None = None) -> str:
    """Pre-warm the tag index for whatever instance the router will resolve to."""
    base_url = rq._current_config.get("base_url") or rq.settings.so_base_url
    key = rq.tag_cache_key(base_url, team)
    rq._tag_index_cache[key] = {"tags": tags, "at": datetime.utcnow(), "ok": True}
    return key


def test_validate_tags_uses_cache_without_refetch(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pre-warm a fresh, ok cache; the client must NOT be called even if it would raise.
    _warm_cache({"python": 7})

    def _boom(**_kw: Any) -> _FakeClient:
        raise AssertionError("SOClient should not be constructed when cache is fresh")

    monkeypatch.setattr(rq, "SOClient", _boom)
    r = _client().get("/api/questions/validate-tags", params={"tags": "python,ruby"})
    by_tag = {v["tag"]: v["status"] for v in r.json()}
    assert by_tag["python"] == "available"
    assert by_tag["ruby"] == "unavailable"


def test_cache_is_not_shared_across_instances(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pointing Settings at a different instance must re-fetch rather than keep
    validating against the previous instance's tag list for the rest of the TTL."""
    _warm_cache({"python": 7})
    original = rq._current_config.get("base_url")
    try:
        rq._current_config["base_url"] = "https://other-instance.example.com/api/v3"
        monkeypatch.setattr(
            rq, "SOClient", lambda **_kw: _FakeClient(tags=[{"name": "ruby", "questionCount": 3}])
        )
        r = _client().get("/api/questions/validate-tags", params={"tags": "python,ruby"})
        by_tag = {v["tag"]: v["status"] for v in r.json()}
        # "python" belonged to the OLD instance only — it must not leak through.
        assert by_tag["python"] == "unavailable"
        assert by_tag["ruby"] == "available"
    finally:
        if original is None:
            rq._current_config.pop("base_url", None)
        else:
            rq._current_config["base_url"] = original
