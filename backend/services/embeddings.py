"""
Embedding service — Ollama nomic-embed-text.

_RETRY_WAIT_MIN and _RETRY_WAIT_MAX are module-level constants so tests
can monkeypatch them to 0 without redefining the retry logic.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.settings import settings

log = structlog.get_logger("soinsight.embeddings")

_EMBED_MODEL = "nomic-embed-text"
_RETRY_ATTEMPTS = 4
_RETRY_WAIT_MIN = 1    # seconds — monkeypatch to 0 in tests
_RETRY_WAIT_MAX = 30   # seconds


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


def build_embed_text(title: str, body: str) -> str:
    """Canonical embedding input: title + first 300 chars of body."""
    return f"{title} {body[:300]}".strip()


class EmbeddingService:
    """Generates embeddings via Ollama nomic-embed-text."""

    def __init__(
        self,
        ollama_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._ollama_url = (ollama_url or settings.ollama_url).rstrip("/")
        self._transport = transport  # None → real network; MockTransport in tests

    async def embed(self, text: str) -> list[float]:
        """Return the embedding vector for *text*. Retried on 429/5xx/timeout."""
        result: list[float] = []
        # Client created once per call; retries reuse the same connection pool.
        async with httpx.AsyncClient(timeout=60.0, transport=self._transport) as http:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception(_is_retryable),
                stop=stop_after_attempt(_RETRY_ATTEMPTS),
                wait=wait_exponential(multiplier=1, min=_RETRY_WAIT_MIN, max=_RETRY_WAIT_MAX),
                reraise=True,
            ):
                with attempt:
                    r = await http.post(
                        f"{self._ollama_url}/api/embeddings",
                        json={"model": _EMBED_MODEL, "prompt": text},
                    )
                    r.raise_for_status()
                    data: dict[str, Any] = r.json()
                    result = list(data["embedding"])
        return result

    async def embed_question(self, title: str, body: str) -> list[float]:
        """Embed title + first 300 chars of body."""
        return await self.embed(build_embed_text(title, body))
