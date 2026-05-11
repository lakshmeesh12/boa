"""In-process event bus — pub/sub for streaming execution events to WebSocket clients.

Each test session gets its own asyncio.Queue. The orchestrator engine
publishes events; the WebSocket router subscribes and fans them out to
connected browser clients.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

_queues: dict[str, list[asyncio.Queue]] = {}


def subscribe(session_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _queues.setdefault(session_id, []).append(q)
    return q


def unsubscribe(session_id: str, q: asyncio.Queue) -> None:
    if session_id in _queues:
        try:
            _queues[session_id].remove(q)
        except ValueError:
            pass
        if not _queues[session_id]:
            del _queues[session_id]


async def publish(session_id: str, event_type: str, data: Any) -> None:
    """Publish to all subscribers of a session. Non-blocking (drops if full)."""
    payload = json.dumps({
        "type": event_type,
        "session_id": session_id,
        "ts": time.time(),
        "data": data,
    })
    for q in list(_queues.get(session_id, [])):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass  # slow client — drop rather than back-pressure the engine


# ─── Convenience typed publishers ────────────────────────────────────────
async def emit_log(session_id: str, message: str, level: str = "INFO", agent: str = "") -> None:
    await publish(session_id, "log", {"message": message, "level": level, "agent": agent})


async def emit_test_result(session_id: str, result: dict) -> None:
    await publish(session_id, "test_result", result)


async def emit_state_change(session_id: str, new_state: str, detail: str = "") -> None:
    await publish(session_id, "state_change", {"state": new_state, "detail": detail})


async def emit_plan(session_id: str, plan: dict) -> None:
    await publish(session_id, "plan_ready", plan)


async def emit_clarification_request(session_id: str, question: str) -> None:
    await publish(session_id, "clarification_request", {"question": question})


async def emit_report_ready(session_id: str, report_id: str) -> None:
    await publish(session_id, "report_ready", {"report_id": report_id})


async def emit_ui_frame(session_id: str, frame_b64: str) -> None:
    """High-frequency frames from the CDP screencast for the live UI canvas."""
    await publish(session_id, "ui_frame", {"frame": frame_b64})


async def emit_ui_action_snapshot(session_id: str, action: str, snapshot_b64: str) -> None:
    """One snapshot per Claude action — feeds the clickable action history strip."""
    await publish(session_id, "ui_snapshot", {"action": action, "snapshot": snapshot_b64})
