"""Session management endpoints — create, list, get, approve, reject, clarify, cancel."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from core.logging_config import get_logger
from models.schemas import (
    ApproveRequest, ChangeContext, ClarifyRequest, CreateSessionRequest,
    RejectRequest, Session,
)
from orchestrator import engine, session_store

log = get_logger("SessionsRouter")
router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])

_GITHUB_REPO = "https://github.com/lakshmeesh12/boa"


@router.post("", status_code=201)
async def create_session(body: CreateSessionRequest) -> dict:
    session = Session(
        name=body.name,
        modules=body.modules,
        test_types=body.test_types,
        plan_mode=body.plan_mode,
    )

    # Change-driven runs: compute the diff + Claude analysis up-front so the planner
    # has everything it needs. ChangeAnalyzer caches by HEAD SHA so this is fast
    # if the dashboard already showed the change banner.
    if body.use_change_context:
        try:
            from change_detection import ChangeAnalyzer, GitWatcher
            watcher = GitWatcher()
            analyzer = ChangeAnalyzer(github_repo_url=_GITHUB_REPO)
            cs = await watcher.compute_changeset()
            analysis = await analyzer.analyze(cs, use_cache=True)
            session.change_context = ChangeContext(change_set=cs, analysis=analysis)
            log.info(
                "session.change_context_attached",
                context={"session": session.id, "files": cs.file_count, "risk": analysis.risk_level},
            )
        except Exception as exc:
            log.warning(
                "session.change_context_failed",
                context={"session": session.id, "error": str(exc)},
            )
            # Don't fail the whole request — fall back to a standard plan.

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
