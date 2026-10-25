"""
Shared plumbing for talking to Ollama.

Every service that calls Ollama (classifier, remediation, embeddings, the
health checks) used to build its own ``httpx.AsyncClient`` with default
settings. Two problems followed from that, both seen in the field:

* ``httpx`` reads ``HTTP(S)_PROXY`` and ``SSL_CERT_FILE`` from the environment
  by default. On a corporate laptop those point at a TLS-intercepting proxy and
  its CA bundle, so a *localhost* Ollama call was either routed to the proxy or
  died at client construction with ``[Errno 2] No such file or directory`` when
  ``SSL_CERT_FILE`` named a missing file. The classifier swallowed that error
  and filed every question under Misuse / Noise. ``make_client`` ignores the
  environment for loopback and single-label (docker-compose service) hosts,
  where a proxy can never be the right route.

* Small models need explicit runtime options to behave. Ollama's default context
  window is smaller than SOInsight's prompts on many versions, and an
  overflowing prompt is truncated from the *front* — exactly where the taxonomy
  and instructions live. ``generation_options`` pins the context size,
  deterministic sampling and an output cap on every request.

Newer request fields (a JSON-schema ``format`` for constrained decoding, and
``think: false`` for reasoning models) are rejected by older Ollama builds.
``downgrade_payload`` strips them so a single 400 costs one retry, after which
the base URL is remembered as legacy and never sent them again.
"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlparse

import httpx

from app.settings import settings

# Base URLs that rejected the optional fields above — skip them from then on.
_LEGACY_SERVERS: set[str] = set()

_CAPABILITY_HINTS = ("format", "schema", "grammar", "think", "json")


class OllamaUnavailable(RuntimeError):
    """Ollama could not serve the request at all (unreachable, or model missing).

    Distinct from "the model answered badly": callers must not record anything
    on the strength of this, because nothing was actually classified.
    """


def _is_local_host(host: str) -> bool:
    if not host:
        return True
    if host == "localhost" or ("." not in host and ":" not in host):
        # Loopback by name, or a single-label name such as a docker-compose
        # service ("ollama") — neither is ever reachable through a proxy.
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def trust_env_for(url: str) -> bool:
    """Whether an httpx client for *url* should honour proxy/CA env vars."""
    return not _is_local_host(urlparse(url).hostname or "")


def make_client(
    base_url: str,
    timeout: float | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Build an AsyncClient for an Ollama server at *base_url*."""
    return httpx.AsyncClient(
        timeout=timeout if timeout is not None else float(settings.ollama_timeout),
        transport=transport,
        trust_env=trust_env_for(base_url),
    )


def generation_options(num_predict: int | None = None) -> dict[str, Any]:
    """Deterministic, context-safe sampling options for structured output."""
    opts: dict[str, Any] = {
        "temperature": 0,
        "top_k": 1,
        "top_p": 1,
        "seed": 0,
        "num_ctx": int(settings.ollama_num_ctx),
    }
    if num_predict:
        opts["num_predict"] = int(num_predict)
    return opts


def build_generate_payload(
    base_url: str,
    *,
    model: str,
    prompt: str,
    system: str | None = None,
    schema: dict[str, Any] | None = None,
    num_predict: int | None = None,
) -> dict[str, Any]:
    """A /api/generate body. *schema* enables grammar-constrained decoding."""
    legacy = base_url.rstrip("/") in _LEGACY_SERVERS
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "format": schema if (schema is not None and not legacy) else "json",
        "stream": False,
        "options": generation_options(num_predict),
        "keep_alive": settings.ollama_keep_alive,
    }
    if system:
        payload["system"] = system
    if not legacy:
        # Reasoning models (qwen3, deepseek-r1, ...) otherwise spend most of the
        # output budget on a hidden chain of thought before the JSON appears.
        payload["think"] = False
    return payload


def is_capability_error(exc: BaseException) -> bool:
    """True when Ollama rejected a request field rather than failing to serve."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    status = exc.response.status_code
    if status == 400:
        return True
    if status != 500:
        return False
    try:
        text = exc.response.text.lower()
    except Exception:
        return False
    return any(h in text for h in _CAPABILITY_HINTS)


def has_optional_fields(payload: dict[str, Any]) -> bool:
    return isinstance(payload.get("format"), dict) or "think" in payload


def downgrade_payload(base_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Strip fields older Ollama builds reject, and remember the server."""
    _LEGACY_SERVERS.add(base_url.rstrip("/"))
    out = dict(payload)
    out["format"] = "json"
    out.pop("think", None)
    return out


def is_model_missing(exc: BaseException) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code != 404:
        return False
    try:
        return "not found" in exc.response.text.lower()
    except Exception:
        return True


def is_unreachable(exc: BaseException) -> bool:
    """Connection-level failure: nothing reached the model."""
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.UnsupportedProtocol))
