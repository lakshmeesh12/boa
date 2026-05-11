"""In-memory session store with JSON persistence for crash recovery."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from core.logging_config import get_logger
from core.settings import settings
from models.schemas import Session, SessionState

log = get_logger("SessionStore")

_sessions: dict[str, Session] = {}
_sessions_dir = settings.data_dir / "sessions"
_sessions_dir.mkdir(parents=True, exist_ok=True)


def create(session: Session) -> Session:
    _sessions[session.id] = session
    _persist(session)
    log.info("session.created", context={"id": session.id, "name": session.name})
    return session


def get(session_id: str) -> Optional[Session]:
    if session_id in _sessions:
        return _sessions[session_id]
    # Try disk recovery
    path = _sessions_dir / f"{session_id}.json"
    if path.exists():
        try:
            s = Session.model_validate_json(path.read_text())
            _sessions[session_id] = s
            return s
        except Exception:
            pass
    return None


def update(session: Session) -> None:
    _sessions[session.id] = session
    _persist(session)


def list_all() -> list[Session]:
    return sorted(_sessions.values(), key=lambda s: s.created_at, reverse=True)


def delete(session_id: str) -> bool:
    _sessions.pop(session_id, None)
    path = _sessions_dir / f"{session_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def _persist(session: Session) -> None:
    path = _sessions_dir / f"{session.id}.json"
    try:
        path.write_text(session.model_dump_json(), encoding="utf-8")
    except Exception as exc:
        log.warning("session.persist_failed", context={"error": str(exc)})
