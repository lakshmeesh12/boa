"""Script upload endpoints."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from core.settings import settings
from models.schemas import ScriptType, UploadedScript
from orchestrator import session_store

router = APIRouter(prefix="/api/v1/sessions/{session_id}/scripts", tags=["scripts"])

_ALLOWED_EXT = {".sh", ".bash", ".py", ".feature"}
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


def _detect_type(filename: str) -> ScriptType:
    ext = Path(filename).suffix.lower()
    if ext in (".sh", ".bash"):
        return ScriptType.BASH
    if ext == ".feature":
        return ScriptType.FEATURE
    return ScriptType.PYTHON


@router.post("/upload", status_code=201)
async def upload_script(session_id: str, file: UploadFile = File(...)) -> dict:
    s = session_store.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    content = await file.read()
    if len(content) > _MAX_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 5 MB)")

    script_dir = settings.scripts_dir / session_id
    script_dir.mkdir(parents=True, exist_ok=True)
    dest = script_dir / (file.filename or f"script_{uuid.uuid4().hex}{ext}")
    dest.write_bytes(content)

    script = UploadedScript(
        filename=file.filename or dest.name,
        script_type=_detect_type(file.filename or ""),
        file_path=str(dest),
        size_bytes=len(content),
    )
    s.uploaded_scripts.append(script)
    session_store.update(s)

    return script.model_dump(mode="json")


@router.get("")
async def list_scripts(session_id: str) -> dict:
    s = session_store.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    return {"scripts": [sc.model_dump(mode="json") for sc in s.uploaded_scripts]}


@router.patch("/{script_id}/toggle")
async def toggle_script(session_id: str, script_id: str, enabled: bool) -> dict:
    s = session_store.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    for sc in s.uploaded_scripts:
        if sc.id == script_id:
            sc.enabled = enabled
            session_store.update(s)
            return {"script_id": script_id, "enabled": enabled}
    raise HTTPException(status_code=404, detail="script not found")


@router.delete("/{script_id}")
async def delete_script(session_id: str, script_id: str) -> dict:
    s = session_store.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    for i, sc in enumerate(s.uploaded_scripts):
        if sc.id == script_id:
            Path(sc.file_path).unlink(missing_ok=True)
            s.uploaded_scripts.pop(i)
            session_store.update(s)
            return {"deleted": script_id}
    raise HTTPException(status_code=404, detail="script not found")
