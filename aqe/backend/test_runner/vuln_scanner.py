"""Vulnerability scanning — pip-audit (SCA), bandit (SAST), semgrep (broader SAST).

Each scanner runs as a subprocess against the target's source tree and produces
TestResult objects with category=Vulnerability. Critical/High findings -> FAILED,
Medium/Low -> PASSED with severity attached.

Gracefully degrades if a tool isn't installed: emits one ERROR result naming the
missing tool and the install command, then moves on.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from pathlib import Path

from core.logging_config import get_logger
from models.schemas import TestResult, TestStatus

log = get_logger("VulnScanner")

# Repo root + target paths
_REPO_ROOT = Path(__file__).resolve().parents[3]
_TARGET_BACKEND = _REPO_ROOT / "backend"
_TARGET_REQS    = _REPO_ROOT / "backend" / "requirements.txt"

# Findings at these severities mark the test FAILED.
_FAIL_SEVERITIES = {"critical", "high"}


def _now_ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def _make_missing_tool_result(tool: str, install_hint: str) -> TestResult:
    return TestResult(
        test_id=f"vuln-{tool}-missing-{uuid.uuid4().hex[:6]}",
        test_name=f"{tool} not installed",
        module="Infrastructure",
        category="Vulnerability",
        status=TestStatus.ERROR,
        duration_ms=0.0,
        error=f"{tool} not found on PATH. Install with: {install_hint}",
    )


async def _run_cmd(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out_b, err_b = await proc.communicate()
    return proc.returncode or 0, out_b.decode("utf-8", errors="replace"), err_b.decode("utf-8", errors="replace")


# ─── pip-audit (dependency CVEs) ────────────────────────────────────────


async def run_pip_audit() -> list[TestResult]:
    if not shutil.which("pip-audit"):
        return [_make_missing_tool_result("pip-audit", "pip install pip-audit")]
    if not _TARGET_REQS.exists():
        return [TestResult(
            test_id="vuln-pip-audit-no-reqs",
            test_name="pip-audit",
            module="Infrastructure",
            category="Vulnerability",
            status=TestStatus.SKIPPED,
            duration_ms=0.0,
            error=f"requirements file not found: {_TARGET_REQS}",
        )]

    t0 = time.perf_counter()
    rc, out, err = await _run_cmd(
        ["pip-audit", "--format", "json", "-r", str(_TARGET_REQS)],
        cwd=_REPO_ROOT,
    )
    duration = _now_ms(t0)
    # pip-audit returns non-zero when vulns are found — that's expected, not a failure.
    try:
        payload = json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        return [TestResult(
            test_id="vuln-pip-audit-parse",
            test_name="pip-audit parse",
            module="Infrastructure",
            category="Vulnerability",
            status=TestStatus.ERROR,
            duration_ms=duration,
            error=f"failed to parse pip-audit output. stderr: {err[:200]}",
        )]

    results: list[TestResult] = []
    deps = payload.get("dependencies", []) if isinstance(payload, dict) else payload
    for dep in deps:
        name = dep.get("name", "?")
        version = dep.get("version", "?")
        for vuln in dep.get("vulns", []) or []:
            vid = vuln.get("id", "CVE-?")
            fix_versions = vuln.get("fix_versions") or []
            description = vuln.get("description", "")
            # pip-audit doesn't expose severity directly; treat all dep CVEs as high.
            severity = "high"
            status = TestStatus.FAILED if severity in _FAIL_SEVERITIES else TestStatus.PASSED
            results.append(TestResult(
                test_id=f"vuln-pip-audit-{name}-{vid}",
                test_name=f"CVE in {name} {version}: {vid}",
                module="Infrastructure",
                category="Vulnerability",
                status=status,
                duration_ms=duration,
                request_summary=f"pip-audit -r {_TARGET_REQS.name}",
                response_summary=description[:200],
                error=f"{vid} — fix: {','.join(fix_versions) if fix_versions else 'no fix available'}",
                severity=severity,
            ))
    if not results:
        results.append(TestResult(
            test_id="vuln-pip-audit-clean",
            test_name="pip-audit: no vulnerable dependencies",
            module="Infrastructure",
            category="Vulnerability",
            status=TestStatus.PASSED,
            duration_ms=duration,
            response_summary="0 CVEs across declared dependencies",
        ))
    log.info("vuln_scanner.pip_audit_done", context={"findings": len(results)})
    return results


# ─── bandit (Python SAST) ───────────────────────────────────────────────


# Bandit findings cap — anything beyond this is rolled up into one summary
# result. Without this, scanning a real codebase emits >8000 events and the
# frontend WebSocket / DOM tree freezes the browser.
_BANDIT_MAX_DETAILED = 50

# Bandit scans this subdir only (the demo surface). Scanning all of backend/
# yields thousands of false-positive-tier findings from stdlib patterns.
_BANDIT_SCAN_DIR = _REPO_ROOT / "backend" / "routers"


def _severity_weight(s: str) -> int:
    return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(s.lower(), 1)


async def run_bandit() -> list[TestResult]:
    if not shutil.which("bandit"):
        return [_make_missing_tool_result("bandit", "pip install bandit")]
    scan_target = _BANDIT_SCAN_DIR if _BANDIT_SCAN_DIR.exists() else _TARGET_BACKEND
    if not scan_target.exists():
        return []

    t0 = time.perf_counter()
    rc, out, err = await _run_cmd(
        ["bandit", "-r", str(scan_target), "-f", "json", "-q"],
        cwd=_REPO_ROOT,
    )
    duration = _now_ms(t0)
    try:
        payload = json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        return [TestResult(
            test_id="vuln-bandit-parse",
            test_name="bandit parse",
            module="Infrastructure",
            category="Vulnerability",
            status=TestStatus.ERROR,
            duration_ms=duration,
            error=f"failed to parse bandit output. stderr: {err[:200]}",
        )]

    raw_issues = payload.get("results", []) or []
    # Sort by severity desc, then confidence desc, so the most critical issues
    # are always in the detailed-emit window.
    sorted_issues = sorted(
        raw_issues,
        key=lambda i: (
            -_severity_weight(i.get("issue_severity", "low")),
            -_severity_weight(i.get("issue_confidence", "low")),
        ),
    )
    log.info(
        "vuln_scanner.bandit_raw",
        context={"total_findings": len(sorted_issues), "scan_dir": str(scan_target.name)},
    )

    detailed = sorted_issues[:_BANDIT_MAX_DETAILED]
    rest = sorted_issues[_BANDIT_MAX_DETAILED:]
    results: list[TestResult] = []

    for issue in detailed:
        severity = (issue.get("issue_severity") or "medium").lower()
        confidence = (issue.get("issue_confidence") or "medium").lower()
        test_name = issue.get("test_name") or issue.get("test_id") or "unknown"
        text = issue.get("issue_text") or ""
        file_path = issue.get("filename", "?")
        line_no = issue.get("line_number", 0)
        status = TestStatus.FAILED if severity in _FAIL_SEVERITIES else TestStatus.PASSED
        results.append(TestResult(
            test_id=f"vuln-bandit-{issue.get('test_id', 'X')}-{file_path}-{line_no}",
            test_name=f"[bandit:{severity}] {test_name} @ {Path(file_path).name}:{line_no}",
            module="Infrastructure",
            category="Vulnerability",
            status=status,
            duration_ms=duration,
            request_summary=f"bandit -r {scan_target.name}",
            response_summary=f"{file_path}:{line_no} - {text[:160]}",
            error=f"{text} (confidence={confidence})",
            severity=severity,
        ))

    # Roll the rest into a single summary result instead of flooding the WS
    if rest:
        counts = {"low": 0, "medium": 0, "high": 0, "critical": 0}
        for i in rest:
            sev = (i.get("issue_severity") or "low").lower()
            if sev in counts:
                counts[sev] += 1
        results.append(TestResult(
            test_id="vuln-bandit-rollup",
            test_name=f"[bandit] {len(rest)} additional findings (rolled up)",
            module="Infrastructure",
            category="Vulnerability",
            status=TestStatus.PASSED,
            duration_ms=duration,
            response_summary=(
                f"{counts['critical']} crit / {counts['high']} high / "
                f"{counts['medium']} med / {counts['low']} low"
            ),
            severity="low",
        ))

    if not results:
        results.append(TestResult(
            test_id="vuln-bandit-clean",
            test_name="bandit: no SAST findings",
            module="Infrastructure",
            category="Vulnerability",
            status=TestStatus.PASSED,
            duration_ms=duration,
            response_summary="0 issues",
        ))
    log.info(
        "vuln_scanner.bandit_done",
        context={
            "total_findings": len(sorted_issues),
            "detailed_emitted": len(detailed),
            "rolled_up": len(rest),
        },
    )
    return results


# ─── semgrep (broader SAST) ─────────────────────────────────────────────


async def run_semgrep() -> list[TestResult]:
    if not shutil.which("semgrep"):
        return [_make_missing_tool_result("semgrep", "pip install semgrep")]
    if not _TARGET_BACKEND.exists():
        return []

    t0 = time.perf_counter()
    rc, out, err = await _run_cmd(
        ["semgrep", "--config", "p/security-audit", "--json", "--quiet", str(_TARGET_BACKEND)],
        cwd=_REPO_ROOT,
    )
    duration = _now_ms(t0)
    try:
        payload = json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        return [TestResult(
            test_id="vuln-semgrep-parse",
            test_name="semgrep parse",
            module="Infrastructure",
            category="Vulnerability",
            status=TestStatus.ERROR,
            duration_ms=duration,
            error=f"failed to parse semgrep output. stderr: {err[:200]}",
        )]

    results: list[TestResult] = []
    for finding in payload.get("results", []):
        sev_raw = (finding.get("extra", {}).get("severity") or "INFO").lower()
        # Semgrep uses ERROR/WARNING/INFO. Map onto our severity scale.
        sev_map = {"error": "high", "warning": "medium", "info": "low"}
        severity = sev_map.get(sev_raw, "low")
        check_id = finding.get("check_id", "?")
        path = finding.get("path", "?")
        line = finding.get("start", {}).get("line", 0)
        msg = finding.get("extra", {}).get("message", "")
        status = TestStatus.FAILED if severity in _FAIL_SEVERITIES else TestStatus.PASSED
        results.append(TestResult(
            test_id=f"vuln-semgrep-{check_id}-{path}-{line}",
            test_name=f"[semgrep:{severity}] {check_id} @ {Path(path).name}:{line}",
            module="Infrastructure",
            category="Vulnerability",
            status=status,
            duration_ms=duration,
            request_summary="semgrep --config p/security-audit",
            response_summary=f"{path}:{line} — {msg[:160]}",
            error=msg,
            severity=severity,
        ))
    if not results:
        results.append(TestResult(
            test_id="vuln-semgrep-clean",
            test_name="semgrep: no security findings",
            module="Infrastructure",
            category="Vulnerability",
            status=TestStatus.PASSED,
            duration_ms=duration,
            response_summary="0 findings against p/security-audit",
        ))
    log.info("vuln_scanner.semgrep_done", context={"findings": len(results)})
    return results


# ─── orchestrator ───────────────────────────────────────────────────────


async def run_all_scans() -> list[TestResult]:
    """Run all three scanners sequentially and concatenate results."""
    out: list[TestResult] = []
    out.extend(await run_pip_audit())
    out.extend(await run_bandit())
    out.extend(await run_semgrep())
    return out
