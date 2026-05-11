"""AQE Platform — FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from core.logging_config import configure_logging, get_logger
from core.settings import settings

configure_logging()
log = get_logger("AQE.Bootstrap")

# Silence Neo4j cartesian-product INFO notifications — they are not errors
logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("aqe.starting", context={"port": settings.aqe_api_port})
    # GraphRAG init
    from graphrag import neo4j_engine, qdrant_engine, log_ingestion
    await qdrant_engine.ensure_collection()
    await neo4j_engine.ensure_schema()
    await log_ingestion.start_ingestion()
    log.info("aqe.ready")
    yield
    log.info("aqe.shutting_down")
    await log_ingestion.stop_ingestion()


app = FastAPI(
    title="AQE — Autonomous Quality Engineering Platform",
    description="AI-driven testing framework for the Core Banking Simulator.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── API Routers ──────────────────────────────────────────────────────────
from routers import sessions, reports, graphrag, stream, scripts  # noqa: E402

app.include_router(sessions.router)
app.include_router(reports.router)
app.include_router(graphrag.router)
app.include_router(stream.router)

# Scripts router is nested under sessions — register separately
from fastapi import APIRouter  # noqa: E402
app.include_router(scripts.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    import httpx
    from datetime import datetime, timezone
    checks: dict = {}

    # Target API
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{settings.target_api_url}/health")
            checks["target_api"] = "ok" if r.status_code == 200 else "degraded"
    except Exception:
        checks["target_api"] = "unreachable"

    # Qdrant
    try:
        from graphrag.qdrant_engine import _get_qdrant
        _get_qdrant().get_collections()
        checks["qdrant"] = "ok"
    except Exception:
        checks["qdrant"] = "unreachable"

    # Neo4j
    try:
        from graphrag.neo4j_engine import _get_driver
        _get_driver().verify_connectivity()
        checks["neo4j"] = "ok"
    except Exception:
        checks["neo4j"] = "unreachable"

    checks["aqe_api"] = "ok"
    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": overall, "checks": checks, "timestamp": datetime.now(timezone.utc).isoformat()}


# ─── Serve frontend SPA ───────────────────────────────────────────────────
# Use a catch-all FileResponse route INSTEAD of StaticFiles mount at "/".
# StaticFiles mounted at "/" shadows all API routes (including /health) in
# Starlette because it acts as a catch-all prefix match before FastAPI's
# own router gets a chance. A catch-all route defined here comes AFTER all
# @app.get() routes in the route table, so API endpoints win correctly.
_frontend_dir = Path(__file__).resolve().parents[1] / "frontend"


@app.get("/{full_path:path}", include_in_schema=False)
async def serve_spa(full_path: str) -> FileResponse:
    target = _frontend_dir / full_path
    if target.is_file():
        return FileResponse(str(target))
    return FileResponse(str(_frontend_dir / "index.html"))
