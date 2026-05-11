"""Background log ingestion — tails the banking simulator API log and feeds Qdrant + Neo4j."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from core.logging_config import get_logger

log = get_logger("LogIngestion")

_running = False
_ingested_count = 0
_last_ingested_ts: str = ""


async def start_ingestion() -> None:
    global _running
    if _running:
        return
    _running = True
    asyncio.create_task(_ingest_loop())
    log.info("log_ingestion.started")


async def _ingest_loop() -> None:
    global _running, _ingested_count, _last_ingested_ts

    # Strategy 1: try docker exec (container running)
    # Strategy 2: fall back to reading volume file directly on the host
    log_path = Path("/var/logs/bank-simulator/api.log")

    while _running:
        try:
            if log_path.exists():
                await _ingest_file(log_path)
            else:
                await _ingest_via_docker()
        except Exception as exc:
            log.warning("log_ingestion.error", context={"error": str(exc)})
        await asyncio.sleep(15)  # poll every 15s


async def _ingest_file(path: Path) -> None:
    global _ingested_count, _last_ingested_ts
    from graphrag.qdrant_engine import upsert_log_entry
    from graphrag.neo4j_engine import record_error

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return

    # Only process last 500 lines to stay responsive
    for raw in lines[-500:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue

        ok = await upsert_log_entry(entry)
        if ok:
            _ingested_count += 1
            _last_ingested_ts = entry.get("timestamp", "")

        if entry.get("level") in ("ERROR", "CRITICAL") and entry.get("trace_id"):
            await record_error(
                session_id="system",
                test_id=entry.get("trace_id", ""),
                trace_id=entry.get("trace_id", ""),
                message=entry.get("message", ""),
                level=entry.get("level", "ERROR"),
            )


async def _ingest_via_docker() -> None:
    """Tail logs from the bank-api container via docker exec."""
    global _ingested_count, _last_ingested_ts
    from graphrag.qdrant_engine import upsert_log_entry

    try:
        result = await asyncio.create_subprocess_exec(
            "docker", "exec", "bank-api",
            "tail", "-n", "200", "/var/logs/bank-simulator/api.log",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(result.communicate(), timeout=10)
        for raw in stdout.decode("utf-8", errors="replace").splitlines():
            if not raw.strip():
                continue
            try:
                entry = json.loads(raw)
                ok = await upsert_log_entry(entry)
                if ok:
                    _ingested_count += 1
                    _last_ingested_ts = entry.get("timestamp", "")
            except json.JSONDecodeError:
                pass
    except (asyncio.TimeoutError, FileNotFoundError):
        pass


def get_status() -> dict:
    return {
        "running": _running,
        "ingested_total": _ingested_count,
        "last_ingested_at": _last_ingested_ts,
    }


async def stop_ingestion() -> None:
    global _running
    _running = False
