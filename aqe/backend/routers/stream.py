"""WebSocket endpoint — live execution event stream per session."""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core import event_bus

router = APIRouter(tags=["stream"])


@router.websocket("/api/v1/sessions/{session_id}/ws")
async def session_stream(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    queue = event_bus.subscribe(session_id)
    try:
        while True:
            message = await queue.get()
            await websocket.send_text(message)
    except WebSocketDisconnect:
        pass
    finally:
        event_bus.unsubscribe(session_id, queue)
