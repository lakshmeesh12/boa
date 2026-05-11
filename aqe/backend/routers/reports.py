from __future__ import annotations
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from core.settings import settings

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


@router.get("")
async def list_reports() -> dict:
    reports = []
    for p in sorted(settings.reports_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text())
            reports.append({
                "id": data["id"], "session_id": data["session_id"],
                "session_name": data.get("session_name",""),
                "total": data["total"], "passed": data["passed"],
                "failed": data["failed"], "errors": data["errors"],
                "duration_seconds": data.get("duration_seconds",0),
                "created_at": data["created_at"],
            })
        except Exception:
            pass
    return {"reports": reports, "total": len(reports)}


@router.get("/{report_id}")
async def get_report(report_id: str) -> dict:
    path = settings.reports_dir / f"{report_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="report not found")
    return json.loads(path.read_text())


@router.get("/{report_id}/html", response_class=HTMLResponse)
async def get_report_html(report_id: str) -> str:
    path = settings.reports_dir / f"{report_id}.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="HTML report not found")
    return path.read_text(encoding="utf-8")
