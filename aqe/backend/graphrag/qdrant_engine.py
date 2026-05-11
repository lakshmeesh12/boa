"""Qdrant vector store — embed and search banking API log entries."""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from core.logging_config import get_logger
from core.settings import settings

log = get_logger("QdrantEngine")

_client = None
_openai_client = None
_collection_ready = False


def _get_qdrant():
    global _client
    if _client is None:
        from qdrant_client import QdrantClient
        kwargs: dict = {"url": settings.qdrant_url}
        if settings.qdrant_api_key:
            kwargs["api_key"] = settings.qdrant_api_key
        _client = QdrantClient(**kwargs)
    return _client


def _get_openai():
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


async def ensure_collection() -> bool:
    global _collection_ready
    if _collection_ready:
        return True
    try:
        client = _get_qdrant()
        from qdrant_client.models import Distance, VectorParams
        existing = [c.name for c in client.get_collections().collections]
        if settings.qdrant_collection not in existing:
            client.create_collection(
                collection_name=settings.qdrant_collection,
                vectors_config=VectorParams(size=settings.embedding_dim, distance=Distance.COSINE),
            )
            log.info("qdrant.collection_created", context={"name": settings.qdrant_collection})
        _collection_ready = True
        return True
    except Exception as exc:
        log.error("qdrant.init_failed", context={"error": str(exc)})
        return False


async def embed(text: str) -> list[float]:
    """Embed text using OpenAI text-embedding-3-large."""
    oai = _get_openai()
    resp = await oai.embeddings.create(model=settings.openai_embedding_model, input=text)
    return resp.data[0].embedding


async def upsert_log_entry(entry: dict) -> bool:
    """Embed and store a single JSON log line in Qdrant."""
    try:
        await ensure_collection()
        text = f"{entry.get('level','')} {entry.get('module','')} {entry.get('message','')}"
        if ctx := entry.get("context"):
            text += " " + str(ctx)[:200]
        vector = await embed(text)
        client = _get_qdrant()
        from qdrant_client.models import PointStruct
        client.upsert(
            collection_name=settings.qdrant_collection,
            points=[PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "timestamp": entry.get("timestamp", ""),
                    "level": entry.get("level", ""),
                    "module": entry.get("module", ""),
                    "message": entry.get("message", ""),
                    "trace_id": entry.get("trace_id", ""),
                    "service": entry.get("service", ""),
                    "raw": str(entry)[:500],
                },
            )],
        )
        return True
    except Exception as exc:
        log.warning("qdrant.upsert_failed", context={"error": str(exc)})
        return False


async def search_logs(query: str, limit: int = 5) -> list[dict]:
    """Semantic search over ingested log entries."""
    try:
        await ensure_collection()
        vector = await embed(query)
        client = _get_qdrant()
        results = client.search(
            collection_name=settings.qdrant_collection,
            query_vector=vector,
            limit=limit,
            with_payload=True,
        )
        return [
            {"score": round(r.score, 4), **r.payload}
            for r in results
        ]
    except Exception as exc:
        log.warning("qdrant.search_failed", context={"error": str(exc)})
        return []


async def get_collection_info() -> dict:
    try:
        client = _get_qdrant()
        info = client.get_collection(settings.qdrant_collection)
        return {
            "collection": settings.qdrant_collection,
            "points_count": info.points_count,
            "status": str(info.status),
        }
    except Exception as exc:
        return {"error": str(exc), "collection": settings.qdrant_collection}
