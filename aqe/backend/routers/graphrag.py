from __future__ import annotations
from fastapi import APIRouter
from models.schemas import GraphRAGQueryRequest
from graphrag import qdrant_engine, neo4j_engine, log_ingestion

router = APIRouter(prefix="/api/v1/graphrag", tags=["graphrag"])


@router.post("/ingest")
async def trigger_ingest() -> dict:
    await log_ingestion.start_ingestion()
    return {"status": "ingestion_started", **log_ingestion.get_status()}


@router.get("/status")
async def graphrag_status() -> dict:
    qdrant_info = await qdrant_engine.get_collection_info()
    return {
        "qdrant": qdrant_info,
        "ingestion": log_ingestion.get_status(),
    }


@router.post("/search")
async def semantic_search(body: GraphRAGQueryRequest) -> dict:
    results = await qdrant_engine.search_logs(body.query, limit=body.limit)
    return {"query": body.query, "results": results}


@router.get("/graph")
async def get_graph() -> dict:
    return await neo4j_engine.get_graph_data()
