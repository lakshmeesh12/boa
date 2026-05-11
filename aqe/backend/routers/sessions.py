"""Session management endpoints — create, list, get, approve, reject, clarify, cancel."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from models.schemas import (
    ApproveRequest, ClarifyRequest, CreateSessionRequest, RejectRequest, Session,
)
from orchestrator import engine, session_store

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


@router.post("", status_code=201)
async def create_session(body: CreateSessionRequest) -> dict:
    session = Session(name=body.name, modules=body.modules, test_types=body.test_types)
    session_store.create(session)
    await engine.start_planning(session)
    return session.model_dump(mode="json")


@router.get("")
async def list_sessions() -> dict:
    sessions = session_store.list_all()
    return {"sessions": [s.model_dump(mode="json") for s in sessions], "total": len(sessions)}


@router.get("/{session_id}")
async def get_session(session_id: str) -> dict:
    s = session_store.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    return s.model_dump(mode="json")


@router.post("/{session_id}/approve")
async def approve(session_id: str, body: ApproveRequest) -> dict:
    s = session_store.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    from models.schemas import ChatMessage
    s.chat_history.append(ChatMessage(role="user", content=body.message))
    await engine.approve(s)
    return {"status": "executing", "session_id": session_id}


@router.post("/{session_id}/reject")
async def reject(session_id: str, body: RejectRequest) -> dict:
    s = session_store.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    await engine.reject(s, body.feedback)
    return {"status": "rejected", "session_id": session_id}


@router.post("/{session_id}/clarify")
async def clarify(session_id: str, body: ClarifyRequest) -> dict:
    s = session_store.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    await engine.clarify(s, body.message)
    return {"status": "clarification_delivered", "session_id": session_id}


@router.delete("/{session_id}")
async def cancel_session(session_id: str) -> dict:
    s = session_store.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    await engine.cancel(s)
    return {"status": "cancelled"}
