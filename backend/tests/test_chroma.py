"""
Tests for services/embeddings.py and services/chroma_store.py.

Ollama calls mocked via httpx.MockTransport.
ChromaDB uses EphemeralClient (in-memory) — no disk I/O, no dimension conflicts.
"""

from __future__ import annotations

import json
import uuid

import chromadb
import httpx
import pytest

from services.chroma_store import DUPLICATE_THRESHOLD, ChromaStore
from services.embeddings import EmbeddingService, build_embed_text

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _embed_resp(embedding: list[float], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps({"embedding": embedding}).encode(),
        headers={"content-type": "application/json"},
    )


def _store() -> ChromaStore:
    """
    Fresh isolated collection per call.
    EphemeralClient shares an in-process backend, so unique collection names
    are required to prevent state leaks between tests.
    """
    return ChromaStore(
        client=chromadb.EphemeralClient(),
        collection_name=f"q_{uuid.uuid4().hex[:8]}",
    )


# ---------------------------------------------------------------------------
# build_embed_text
# ---------------------------------------------------------------------------

def test_build_embed_text_truncates_body_to_300() -> None:
    text = build_embed_text("title", "x" * 400)
    assert "x" * 301 not in text
    assert "x" * 300 in text


def test_build_embed_text_combines_title_and_body() -> None:
    text = build_embed_text("my title", "body text")
    assert "my title" in text
    assert "body text" in text


# ---------------------------------------------------------------------------
# EmbeddingService — Ollama call shape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embed_posts_to_ollama_with_correct_model() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        r = _embed_resp([0.1, 0.2, 0.3])
        r.request = request
        return r

    svc = EmbeddingService(
        ollama_url="http://ollama.test",
        transport=httpx.MockTransport(handler),
    )
    result = await svc.embed("hello world")

    assert len(seen) == 1
    assert seen[0]["model"] == "nomic-embed-text"
    assert seen[0]["prompt"] == "hello world"
    assert result == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_embed_retries_on_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("services.embeddings._RETRY_WAIT_MIN", 0)
    monkeypatch.setattr("services.embeddings._RETRY_WAIT_MAX", 0)

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        status = 500 if call_count < 3 else 200
        r = _embed_resp([0.5, 0.5], status)
        r.request = request
        return r

    svc = EmbeddingService(
        ollama_url="http://ollama.test",
        transport=httpx.MockTransport(handler),
    )
    result = await svc.embed("retry me")
    assert call_count == 3
    assert result == [0.5, 0.5]


@pytest.mark.asyncio
async def test_embed_question_truncates_body() -> None:
    """embed_question must send title + first 300 chars of body, not full body."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["prompt"])
        r = _embed_resp([1.0])
        r.request = request
        return r

    svc = EmbeddingService(
        ollama_url="http://ollama.test",
        transport=httpx.MockTransport(handler),
    )
    await svc.embed_question(title="T", body="x" * 500)
    assert len(seen) == 1
    assert "T" in seen[0]
    assert "x" * 301 not in seen[0]
    assert "x" * 300 in seen[0]


# ---------------------------------------------------------------------------
# ChromaStore — upsert idempotency
# ---------------------------------------------------------------------------

def test_upsert_is_idempotent() -> None:
    store = _store()
    vec = [1.0, 0.0, 0.0, 0.0]
    store.upsert(so_id=1, embedding=vec, document="Q1")
    store.upsert(so_id=1, embedding=vec, document="Q1")  # duplicate — must not add a row
    assert store.count() == 1


def test_upsert_distinct_ids_both_stored() -> None:
    store = _store()
    store.upsert(so_id=1, embedding=[1.0, 0.0, 0.0, 0.0])
    store.upsert(so_id=2, embedding=[0.0, 1.0, 0.0, 0.0])
    assert store.count() == 2


# ---------------------------------------------------------------------------
# ChromaStore — query_similar
# ---------------------------------------------------------------------------

def test_query_similar_empty_returns_empty() -> None:
    store = _store()
    assert store.query_similar([1.0, 0.0, 0.0, 0.0]) == []


def test_query_similar_returns_closest_first() -> None:
    store = _store()
    # so_id=1: identical to query → distance ≈ 0 (nearest)
    # so_id=2: orthogonal → distance = 1.0 (farthest)
    store.upsert(so_id=1, embedding=[1.0, 0.0, 0.0, 0.0])
    store.upsert(so_id=2, embedding=[0.0, 1.0, 0.0, 0.0])

    results = store.query_similar([1.0, 0.0, 0.0, 0.0], k=2)
    assert len(results) == 2
    assert results[0]["so_id"] == 1
    assert results[0]["distance"] < results[1]["distance"]


def test_query_similar_respects_k_limit() -> None:
    store = _store()
    for i in range(5):
        store.upsert(so_id=i + 1, embedding=[float(i == 0), float(i > 0), 0.0, 0.0])
    results = store.query_similar([1.0, 0.0, 0.0, 0.0], k=2)
    assert len(results) == 2


# ---------------------------------------------------------------------------
# ChromaStore — find_duplicates
# ---------------------------------------------------------------------------

def test_find_duplicates_flags_near_identical_vector() -> None:
    store = _store()
    vec_a = [1.0, 0.0, 0.0, 0.0]
    # cosine distance ≈ 0.00005 — well below DUPLICATE_THRESHOLD
    vec_near = [1.0, 0.01, 0.0, 0.0]
    store.upsert(so_id=1, embedding=vec_a)
    store.upsert(so_id=2, embedding=vec_near)

    dupes = store.find_duplicates(so_id=2, embedding=vec_near, threshold=DUPLICATE_THRESHOLD)
    assert 1 in dupes


def test_find_duplicates_ignores_dissimilar_vector() -> None:
    store = _store()
    store.upsert(so_id=1, embedding=[1.0, 0.0, 0.0, 0.0])
    store.upsert(so_id=2, embedding=[0.0, 1.0, 0.0, 0.0])  # orthogonal → distance 1.0

    dupes = store.find_duplicates(
        so_id=2, embedding=[0.0, 1.0, 0.0, 0.0], threshold=DUPLICATE_THRESHOLD
    )
    assert 1 not in dupes


def test_find_duplicates_excludes_self() -> None:
    store = _store()
    vec = [1.0, 0.0, 0.0, 0.0]
    store.upsert(so_id=42, embedding=vec)

    dupes = store.find_duplicates(so_id=42, embedding=vec)
    assert 42 not in dupes


def test_find_duplicates_empty_collection_returns_empty() -> None:
    store = _store()
    dupes = store.find_duplicates(so_id=1, embedding=[1.0, 0.0, 0.0, 0.0])
    assert dupes == []
