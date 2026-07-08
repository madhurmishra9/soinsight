"""
Tests for GET /api/questions/available-tags.

Powers the Fetch page's tag picker: it should return every tag the configured
SO instance has (via the same cached index /validate-tags uses), support a
substring search, and report ok=False (never raise) when the instance is
unreachable so the frontend can fall back to manual entry.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
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


def test_available_tags_returns_all_tags_sorted_by_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    tags = [
        {"name": "python", "questionCount": 10},
        {"name": "docker", "questionCount": 50},
        {"name": "api-gateway", "questionCount": 3},
    ]
    monkeypatch.setattr(rq, "SOClient", lambda **_kw: _FakeClient(tags=tags))

    r = _client().get("/api/questions/available-tags")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["total"] == 3
    names_in_order = [t["tag"] for t in data["tags"]]
    assert names_in_order == ["docker", "python", "api-gateway"]  # sorted by question_count desc


def test_available_tags_search_filters_substring(monkeypatch: pytest.MonkeyPatch) -> None:
    tags = [{"name": "python"}, {"name": "docker"}, {"name": "python-asyncio"}]
    monkeypatch.setattr(rq, "SOClient", lambda **_kw: _FakeClient(tags=tags))

    r = _client().get("/api/questions/available-tags", params={"search": "pyth"})
    data = r.json()
    assert {t["tag"] for t in data["tags"]} == {"python", "python-asyncio"}
    assert data["total"] == 3   # total ignores the search filter


def test_available_tags_ok_false_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rq, "SOClient", lambda **_kw: _FakeClient(raise_on_list=True))

    r = _client().get("/api/questions/available-tags")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["tags"] == []


def test_available_tags_respects_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    tags = [{"name": f"tag{i}", "questionCount": i} for i in range(10)]
    monkeypatch.setattr(rq, "SOClient", lambda **_kw: _FakeClient(tags=tags))

    r = _client().get("/api/questions/available-tags", params={"limit": 3})
    data = r.json()
    assert len(data["tags"]) == 3
    assert data["total"] == 10
    # Top 3 by question_count: tag9, tag8, tag7
    assert [t["tag"] for t in data["tags"]] == ["tag9", "tag8", "tag7"]
