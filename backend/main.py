"""FastAPI entry point for the Target Banking Simulator."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core.db import get_async_client, get_async_db
from core.logging_config import configure_logging, get_logger
from core.middleware import RequestContextMiddleware
from core.settings import settings
from routers import accounts, audit, credit_card_services, credit_cards, customers, deposits, transactions

configure_logging(settings.service_name, settings.log_dir, settings.log_level)
log = get_logger("Bootstrap")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "service.starting",
        context={
            "service": settings.service_name,
            "log_dir": settings.log_dir,
            "database": settings.database_name,
        },
    )
    try:
        client = get_async_client()
        await client.admin.command("ping")
        log.info("mongo.connected")
    except Exception:
        log.exception("mongo.connection_failed")
    yield
    log.info("service.shutting_down")


app = FastAPI(
    title="Target Banking Simulator — Core Banking API",
    description=(
        "POC core-banking API with deposit accounts, credit cards, fixed "
        "deposits, and an atomic transaction ledger. Designed as the target "
        "system for the Autonomous Quality Engineering (AQE) framework."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — the UI is served from a different origin (port 8080) by default
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestContextMiddleware)


# ─── Global error envelope ───────────────────────────────────────────────
@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", "-")
    log.exception(
        "unhandled.exception",
        context={"path": request.url.path, "method": request.method},
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "internal server error", "trace_id": trace_id},
    )


# ─── Health & meta ───────────────────────────────────────────────────────
@app.get("/health", tags=["meta"])
async def health() -> dict:
    db = get_async_db()
    try:
        await db.command("ping")
        db_state = "ok"
    except Exception:
        db_state = "down"
    return {
        "status": "ok" if db_state == "ok" else "degraded",
        "service": settings.service_name,
        "database": db_state,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/", tags=["meta"])
async def root() -> dict:
    return {
        "service": settings.service_name,
        "ui": "served separately on port 8080 (nginx)",
        "docs": "/docs",
        "health": "/health",
    }


# ─── Routers ─────────────────────────────────────────────────────────────
app.include_router(customers.router)
app.include_router(accounts.router)
app.include_router(credit_cards.router)
app.include_router(credit_card_services.router)
app.include_router(deposits.router)
app.include_router(transactions.router)
app.include_router(audit.router)
