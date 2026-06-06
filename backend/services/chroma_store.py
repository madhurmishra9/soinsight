"""
ChromaDB store for question embeddings.

Collection: "questions", keyed by str(so_id).
Distance metric: cosine in [0, 2]; 0 = identical vectors.
Duplicate threshold: DUPLICATE_THRESHOLD = 0.1  (≈ cosine similarity ≥ 0.9).
"""

from __future__ import annotations

from typing import Any

import chromadb
import structlog

from app.settings import settings

log = structlog.get_logger("soinsight.chroma_store")

# Cosine distance ≤ this → near-duplicate (distance = 1 − cosine_similarity).
DUPLICATE_THRESHOLD = 0.1

_COLLECTION_NAME = "questions"


class ChromaStore:
    """Persistent store for question embedding vectors."""

    def __init__(
        self,
        client: Any | None = None,
        collection_name: str = _COLLECTION_NAME,
    ) -> None:
        """
        client: chromadb.ClientAPI instance.
        If None, creates chromadb.PersistentClient from settings.chroma_path.
        Pass chromadb.EphemeralClient() in tests.

        collection_name: override the collection name (tests use unique names to
        avoid state leaks — EphemeralClient shares an in-process backend).
        """
        self._chroma: Any = (
            client if client is not None else chromadb.PersistentClient(path=settings.chroma_path)
        )
        self._collection: Any = self._chroma.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(
        self,
        so_id: int,
        embedding: list[float],
        metadata: dict[str, str | int | float | bool] | None = None,
        document: str = "",
    ) -> None:
        """Upsert an embedding keyed by so_id. Idempotent — safe to re-run."""
        self._collection.upsert(
            ids=[str(so_id)],
            embeddings=[embedding],
            metadatas=[metadata] if metadata else None,
            documents=[document] if document else None,
        )
        log.debug("chroma_upsert", so_id=so_id)

    def query_similar(
        self,
        embedding: list[float],
        k: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Return up to k results ranked by cosine similarity (closest first).
        Each result: {"so_id": int, "distance": float, "metadata": dict}
        """
        total: int = self._collection.count()
        if total == 0:
            return []
        results: Any = self._collection.query(
            query_embeddings=[embedding],
            n_results=min(k, total),
            include=["distances", "metadatas"],
        )
        ids: list[str] = (results.get("ids") or [[]])[0]
        distances: list[float] = (results.get("distances") or [[]])[0]
        metadatas: list[dict[str, Any]] = (results.get("metadatas") or [[]])[0]
        return [
            {"so_id": int(id_), "distance": dist, "metadata": meta}
            for id_, dist, meta in zip(ids, distances, metadatas, strict=False)
        ]

    def find_duplicates(
        self,
        so_id: int,
        embedding: list[float],
        threshold: float = DUPLICATE_THRESHOLD,
        k: int = 5,
    ) -> list[int]:
        """
        Return so_ids that are near-duplicates of this embedding, excluding self.
        A candidate is a duplicate if cosine distance ≤ threshold.
        """
        # +1 so self doesn't consume a result slot when already upserted.
        results = self.query_similar(embedding, k=k + 1)
        duplicates = [
            r["so_id"]
            for r in results
            if r["so_id"] != so_id and r["distance"] <= threshold
        ]
        if duplicates:
            log.info("duplicates_found", so_id=so_id, count=len(duplicates))
        return duplicates

    def count(self) -> int:
        """Number of embeddings stored in the collection."""
        result: int = self._collection.count()
        return result
