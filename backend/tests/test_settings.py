"""
Tests for routers/settings.py — config storage + connection test contract.

The frontend posts {base_url, api_key, team, ollama_url} and expects the /test
response shaped as {reachable, version, scopes: list[str], error?}. SOClient
network calls are stubbed; no real SO instance is contacted.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.settings import _current_config
from routers.settings import router as settings_router


@pytest.fixture(autouse=True)
def _reset_config() -> Any:
    _current_config.clear()
    yield
    _current_config.clear()


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(settings_router)
    return TestClient(app)


def test_post_settings_accepts_frontend_payload_shape(client: TestClient) -> None:
    r = client.post(
        "/api/settings",
        json={
            "base_url": "https://demo.stackenterprise.co/api/v3",
            "api_key": "secret-key",
            "team": "my-team",
            "ollama_url": "http://localhost:11434",
        },
    )
    assert r.status_code == 200
    assert r.json() == {"base_url": "https://demo.stackenterprise.co/api/v3", "team": "my-team"}
    # api_key never echoed back
    assert "api_key" not in r.json()
    assert "secret-key" not in r.text


def test_post_settings_stores_config_for_later_use(client: TestClient) -> None:
    client.post(
        "/api/settings",
        json={"base_url": "https://demo.stackenterprise.co/api/v3", "api_key": "k", "team": "t"},
    )
    assert _current_config["base_url"] == "https://demo.stackenterprise.co/api/v3"
    assert _current_config["api_key"] == "k"
    assert _current_config["team"] == "t"


def test_test_connection_no_config_returns_unreachable(client: TestClient) -> None:
    r = client.get("/api/settings/test")
    assert r.status_code == 200
    data = r.json()
    assert data["reachable"] is False
    assert data["version"] is None
    assert data["scopes"] == []
    assert data["error"]


def test_test_connection_normalizes_reachable_shape(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeClient:
        def __init__(self, base_url: str, auth: Any) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def test_connection(self) -> dict[str, Any]:
            return {"ok": True, "version": "unknown"}

        async def list_scopes(self) -> list[dict[str, Any]]:
            return [{"slug": "team-a", "name": "Team A"}, {"id": 5}]

    monkeypatch.setattr("routers.settings.SOClient", FakeClient)

    client.post(
        "/api/settings",
        json={"base_url": "https://demo.stackenterprise.co/api/v3", "api_key": "k", "team": ""},
    )
    r = client.get("/api/settings/test")
    assert r.status_code == 200
    data = r.json()
    assert data["reachable"] is True
    # "unknown" version normalized to None, not the literal string
    assert data["version"] is None
    assert data["scopes"] == ["Team A", "5"]
