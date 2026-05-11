"""MongoDB connection wrappers (async for FastAPI, sync for the seeder)."""
from __future__ import annotations

import os
from urllib.parse import quote_plus

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import MongoClient
from pymongo.database import Database

load_dotenv()

MONGODB_URI = os.environ.get("MONGODB_URI")
DATABASE_NAME = os.environ.get("DATABASE_NAME", "core_banking")
USER_NAME = os.environ.get("USER_NAME", "").strip()
PASSWORD = os.environ.get("PASSWORD", "").strip()

if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI must be set in environment")


def _resolve_uri() -> str:
    """If credentials are split out into USER_NAME/PASSWORD, splice them in."""
    if USER_NAME and PASSWORD and "@" not in MONGODB_URI:
        u = quote_plus(USER_NAME)
        p = quote_plus(PASSWORD)
        if MONGODB_URI.startswith("mongodb+srv://"):
            host = MONGODB_URI.split("://", 1)[1]
            return f"mongodb+srv://{u}:{p}@{host}"
        host = MONGODB_URI.replace("mongodb://", "")
        return f"mongodb://{u}:{p}@{host}"
    return MONGODB_URI


_resolved = _resolve_uri()


# ── Async client (FastAPI request handlers) ────────────────────────────
_async_client: AsyncIOMotorClient | None = None


def get_async_client() -> AsyncIOMotorClient:
    global _async_client
    if _async_client is None:
        _async_client = AsyncIOMotorClient(_resolved, uuidRepresentation="standard")
    return _async_client


def get_async_db() -> AsyncIOMotorDatabase:
    return get_async_client()[DATABASE_NAME]


# ── Sync client (seeder, scripts) ──────────────────────────────────────
def get_sync_client() -> MongoClient:
    return MongoClient(_resolved, uuidRepresentation="standard")


def get_sync_db() -> Database:
    return get_sync_client()[DATABASE_NAME]
