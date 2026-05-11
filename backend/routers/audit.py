"""Audit log endpoint — retrieve JSON log lines by trace_id.

Reads the structured JSON log file written by the API logger so the AQE
framework can correlate a test execution trace with its exact log lines.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from core.logging_config import get_logger
from core.settings import settings

router = APIRouter(prefix="/api/v1/audit-log", tags=["audit"])
log = get_logger("AuditService")

_LOG_PATH = Path(settings.log_dir) / "api.log"


@router.get("")
async def get_audit_log(
    trace_id: str | None = Query(default=None, description="Filter by trace_id"),
    level: str | None = Query(default=None, description="Filter by log level (e.g. ERROR)"),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """Return matching log entries from the API log file."""
    if not _LOG_PATH.exists():
        log.warning("audit.log_file_missing", context={"path": str(_LOG_PATH)})
        raise HTTPException(status_code=503, detail=f"log file not found at {_LOG_PATH}")

    matched: list[dict] = []
    try:
        with open(_LOG_PATH, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()

        # Read in reverse (newest first) up to a reasonable cap
        for raw_line in reversed(lines[-5000:]):
            line = raw_line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if trace_id and entry.get("trace_id") != trace_id:
                continue
            if level and entry.get("level", "").upper() != level.upper():
                continue

            matched.append(entry)
            if len(matched) >= limit:
                break

    except OSError as exc:
        log.error("audit.read_error", context={"error": str(exc)})
        raise HTTPException(status_code=500, detail=f"failed to read log file: {exc}")

    log.info(
        "audit.queried",
        context={"trace_id": trace_id, "level": level, "results": len(matched)},
    )
    return {
        "query": {"trace_id": trace_id, "level": level, "limit": limit},
        "count": len(matched),
        "entries": matched,
    }
