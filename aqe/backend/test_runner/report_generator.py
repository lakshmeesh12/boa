"""Generate JSON + self-contained HTML reports from a completed session."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from core.logging_config import get_logger
from core.settings import settings
from models.schemas import Report, Session, TestStatus

if TYPE_CHECKING:
    pass

log = get_logger("ReportGenerator")


def _status_color(status: str) -> str:
    return {
        "PASSED": "#22c55e",
        "FAILED": "#ef4444",
        "ERROR":  "#f97316",
        "SKIPPED": "#94a3b8",
        "RUNNING": "#3b82f6",
    }.get(status, "#64748b")


def _status_badge(status: str) -> str:
    color = _status_color(status)
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:9999px;font-size:11px;font-weight:600;">{status}</span>'


def generate(session: Session) -> Report:
    results = session.results
    total   = len(results)
    passed  = sum(1 for r in results if r.status == TestStatus.PASSED)
    failed  = sum(1 for r in results if r.status == TestStatus.FAILED)
    errors  = sum(1 for r in results if r.status == TestStatus.ERROR)

    duration = 0.0
    if session.started_at and session.completed_at:
        duration = (session.completed_at - session.started_at).total_seconds()

    report = Report(
        id=str(uuid.uuid4()),
        session_id=session.id,
        session_name=session.name or session.id[:8],
        modules_tested=list({str(r.module) for r in results}),
        total=total, passed=passed, failed=failed, errors=errors,
        duration_seconds=round(duration, 2),
        results=results,
        ai_executive_summary="",
    )
    return report


def save_json(report: Report) -> Path:
    path = settings.reports_dir / f"{report.id}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    log.info("report.saved_json", context={"report_id": report.id, "path": str(path)})
    return path


def save_html(report: Report) -> Path:
    path = settings.reports_dir / f"{report.id}.html"
    pct = round((report.passed / report.total * 100) if report.total else 0, 1)
    bar_color = "#22c55e" if pct >= 80 else "#f97316" if pct >= 50 else "#ef4444"

    rows = ""
    for r in report.results:
        rca_cell = f'<div style="color:#6b7280;font-size:11px;margin-top:4px;">{r.rca}</div>' if r.rca else ""
        err_cell = f'<div style="color:#ef4444;font-size:11px;">{r.error}</div>' if r.error else ""
        rows += f"""
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;">{r.test_name}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;">{r.module}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;">{_status_badge(r.status)}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;font-family:monospace;">{r.duration_ms}ms</td>
          <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;font-size:12px;color:#475569;">{r.request_summary}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;">{err_cell}{rca_cell}</td>
        </tr>"""

    module_breakdown = ""
    for module in sorted({r.module for r in report.results}):
        m_results = [r for r in report.results if r.module == module]
        m_pass = sum(1 for r in m_results if r.status == TestStatus.PASSED)
        m_fail = len(m_results) - m_pass
        module_breakdown += f"""
        <tr>
          <td style="padding:8px 12px;">{module}</td>
          <td style="padding:8px 12px;">{len(m_results)}</td>
          <td style="padding:8px 12px;color:#22c55e;font-weight:600;">{m_pass}</td>
          <td style="padding:8px 12px;color:#ef4444;font-weight:600;">{m_fail}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><title>AQE Report — {report.session_name}</title>
<style>
  body{{font-family:system-ui,sans-serif;background:#f8fafc;color:#0f172a;margin:0;padding:24px}}
  h1{{color:#0f172a;font-size:1.5rem}} h2{{color:#334155;font-size:1rem;margin-top:32px}}
  table{{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
  th{{background:#f1f5f9;text-align:left;padding:10px 12px;font-size:11px;text-transform:uppercase;color:#64748b}}
  .kpi{{display:inline-block;background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:16px 24px;margin:8px;min-width:100px;text-align:center}}
  .kpi-num{{font-size:2rem;font-weight:700}}
  .summary{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:20px;margin-bottom:24px}}
</style>
</head>
<body>
<div style="display:flex;align-items:center;gap:16px;margin-bottom:24px;">
  <div style="width:36px;height:36px;background:#10b981;border-radius:8px;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:18px;">A</div>
  <div><h1 style="margin:0;">AQE Test Report</h1>
  <div style="color:#64748b;font-size:13px;">Session: {report.session_name} &nbsp;·&nbsp; Generated: {report.created_at.strftime('%Y-%m-%d %H:%M UTC')}</div></div>
</div>

<div class="summary">
  <div style="margin-bottom:16px;font-size:14px;color:#475569;">{report.ai_executive_summary or "No AI summary available."}</div>
  <div>
    <div class="kpi"><div class="kpi-num">{report.total}</div><div style="font-size:11px;color:#64748b;">TOTAL</div></div>
    <div class="kpi"><div class="kpi-num" style="color:#22c55e;">{report.passed}</div><div style="font-size:11px;color:#64748b;">PASSED</div></div>
    <div class="kpi"><div class="kpi-num" style="color:#ef4444;">{report.failed}</div><div style="font-size:11px;color:#64748b;">FAILED</div></div>
    <div class="kpi"><div class="kpi-num" style="color:#f97316;">{report.errors}</div><div style="font-size:11px;color:#64748b;">ERRORS</div></div>
    <div class="kpi"><div class="kpi-num">{pct}%</div><div style="font-size:11px;color:#64748b;">PASS RATE</div></div>
    <div class="kpi"><div class="kpi-num">{report.duration_seconds}s</div><div style="font-size:11px;color:#64748b;">DURATION</div></div>
  </div>
  <div style="margin-top:16px;background:#f1f5f9;border-radius:4px;height:8px;overflow:hidden;">
    <div style="background:{bar_color};width:{pct}%;height:100%;"></div>
  </div>
</div>

<h2>Module Breakdown</h2>
<table><thead><tr><th>Module</th><th>Total</th><th>Passed</th><th>Failed</th></tr></thead>
<tbody>{module_breakdown}</tbody></table>

<h2 style="margin-top:32px;">Test Results</h2>
<table>
  <thead><tr><th>Test Name</th><th>Module</th><th>Status</th><th>Duration</th><th>Request</th><th>Detail / RCA</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
<p style="color:#94a3b8;font-size:11px;margin-top:32px;text-align:center;">
  Generated by AQE Platform · report id: {report.id}
</p>
</body></html>"""

    path.write_text(html, encoding="utf-8")
    log.info("report.saved_html", context={"report_id": report.id})
    return path
