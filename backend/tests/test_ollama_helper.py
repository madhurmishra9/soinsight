"""Tests for services/ollama.py — shared Ollama request plumbing."""

from __future__ import annotations

import httpx

from services import ollama


def test_local_ollama_ignores_proxy_and_ca_env_vars() -> None:
    # A corporate HTTPS_PROXY or a broken SSL_CERT_FILE must never affect a
    # call to a local Ollama (the old "[Errno 2] No such file or directory").
    assert ollama.trust_env_for("http://localhost:11434") is False
    assert ollama.trust_env_for("http://127.0.0.1:11434") is False
    assert ollama.trust_env_for("http://[::1]:11434") is False
    assert ollama.trust_env_for("http://ollama:11434") is False     # compose service
    assert ollama.trust_env_for("https://ollama.corp.example.com") is True


def test_payload_pins_context_and_disables_thinking() -> None:
    p = ollama.build_generate_payload(
        "http://modern.test", model="m", prompt="p", system="s",
        schema={"type": "object"}, num_predict=50,
    )
    assert p["format"] == {"type": "object"}
    assert p["think"] is False
    assert p["system"] == "s"
    assert p["options"]["num_ctx"] >= 4096
    assert p["options"]["num_predict"] == 50
    assert p["keep_alive"]


def test_downgrade_is_remembered_per_server() -> None:
    url = "http://old.test"
    try:
        p = ollama.build_generate_payload(url, model="m", prompt="p", schema={"type": "object"})
        d = ollama.downgrade_payload(url, p)
        assert d["format"] == "json" and "think" not in d
        again = ollama.build_generate_payload(url, model="m", prompt="p", schema={"x": 1})
        assert again["format"] == "json" and "think" not in again
    finally:
        ollama._LEGACY_SERVERS.discard(url)


def _status_error(status: int, text: str) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://o.test/api/generate")
    resp = httpx.Response(status, text=text, request=req)
    return httpx.HTTPStatusError("x", request=req, response=resp)


def test_capability_and_missing_model_detection() -> None:
    assert ollama.is_capability_error(_status_error(400, "invalid format"))
    assert ollama.is_capability_error(_status_error(500, "failed to parse grammar"))
    assert not ollama.is_capability_error(_status_error(500, "out of memory"))
    assert ollama.is_model_missing(_status_error(404, '{"error":"model \'x\' not found"}'))
    assert not ollama.is_model_missing(_status_error(500, "not found"))
