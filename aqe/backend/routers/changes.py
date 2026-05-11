"""Change-detection endpoints.

GET  /api/v1/changes/since-baseline   - current diff + Claude analysis (cached)
POST /api/v1/changes/refresh          - force re-analysis, ignoring cache
GET  /api/v1/changes/status           - lightweight: just file count + head sha
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from change_detection import ChangeAnalyzer, GitWatcher
from core.logging_config import get_logger
from models.schemas import ChangeContext

log = get_logger("ChangesRouter")

router = APIRouter(prefix="/api/v1/changes", tags=["changes"])

_GITHUB_REPO = "https://github.com/lakshmeesh12/boa"

_watcher = GitWatcher()
_analyzer = ChangeAnalyzer(github_repo_url=_GITHUB_REPO)


@router.get("/status")
async def status() -> dict:
    """Quick check — no Claude call, no full diff. Used by the dashboard banner."""
    try:
        cs = await _watcher.compute_changeset()
    except RuntimeError as exc:
        log.warning("changes.status_failed", context={"error": str(exc)})
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "baseline_sha": cs.baseline_sha,
        "head_sha": cs.head_sha,
        "branch": cs.branch,
        "file_count": cs.file_count,
        "is_empty": cs.is_empty,
        "total_additions": cs.total_additions,
        "total_deletions": cs.total_deletions,
        "github_commit_url": f"{_GITHUB_REPO}/commit/{cs.head_sha}" if not cs.is_empty else None,
    }


@router.get("/since-baseline")
async def since_baseline() -> dict:
    """Full ChangeSet + ChangeAnalysis. Cached by HEAD SHA — repeat calls are instant."""
    try:
        cs = await _watcher.compute_changeset()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    analysis = await _analyzer.analyze(cs, use_cache=True)
    ctx = ChangeContext(change_set=cs, analysis=analysis)
    return ctx.model_dump(mode="json")


@router.post("/refresh")
async def refresh() -> dict:
    """Force a fresh Claude analysis, bypassing the SHA cache."""
    try:
        cs = await _watcher.compute_changeset()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    analysis = await _analyzer.analyze(cs, use_cache=False)
    ctx = ChangeContext(change_set=cs, analysis=analysis)
    return ctx.model_dump(mode="json")
