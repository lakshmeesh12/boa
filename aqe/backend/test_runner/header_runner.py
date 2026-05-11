"""HTTP security-header tests.

Probes the target's main endpoints and asserts the presence (and sensible values)
of common security headers. One TestResult per (endpoint, header) pair.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from core.logging_config import get_logger
from core.settings import settings
from models.schemas import TestResult, TestStatus

log = get_logger("HeaderRunner")


@dataclass
class HeaderRule:
    header: str
    required: bool
    description: str
    severity: str = "medium"   # critical | high | medium | low


_HEADER_RULES: list[HeaderRule] = [
    HeaderRule("Strict-Transport-Security", required=False,
               description="HSTS — enforces HTTPS at the browser",
               severity="medium"),
    HeaderRule("Content-Security-Policy", required=True,
               description="CSP — primary XSS mitigation",
               severity="high"),
    HeaderRule("X-Frame-Options", required=True,
               description="Click-jacking protection",
               severity="high"),
    HeaderRule("X-Content-Type-Options", required=True,
               description="Disables MIME-type sniffing",
               severity="medium"),
    HeaderRule("Referrer-Policy", required=False,
               description="Limits referrer leakage",
               severity="low"),
]

# Endpoints to probe — short list covering API root + UI root + sensitive paths
_DEFAULT_PATHS: list[tuple[str, str]] = [
    # (base_kind, path)  base_kind: "api" or "ui"
    ("ui", "/"),
    ("ui", "/login.html"),
    ("api", "/health"),
    ("api", "/api/v1/customers?limit=1"),
]


def _check_rule(rule: HeaderRule, header_value: str | None) -> tuple[TestStatus, str]:
    """Decide pass/fail/skip for a single header rule given its observed value."""
    if header_value is None:
        if rule.required:
            return TestStatus.FAILED, f"Missing required header: {rule.header}"
        return TestStatus.SKIPPED, f"Optional header not set: {rule.header}"
    # Header is present — basic sanity check
    if rule.header == "X-Frame-Options" and header_value.lower() not in {"deny", "sameorigin"}:
        return TestStatus.FAILED, f"X-Frame-Options has unsafe value: {header_value}"
    if rule.header == "X-Content-Type-Options" and "nosniff" not in header_value.lower():
        return TestStatus.FAILED, f"X-Content-Type-Options must contain 'nosniff'; got: {header_value}"
    return TestStatus.PASSED, f"{rule.header}: {header_value[:80]}"


async def run_header_tests() -> list[TestResult]:
    """Probe target endpoints and return one TestResult per (endpoint, header) pair."""
    results: list[TestResult] = []
    api_base = settings.target_api_url.rstrip("/")
    ui_base = settings.target_ui_url.rstrip("/")

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        for kind, path in _DEFAULT_PATHS:
            base = api_base if kind == "api" else ui_base
            url = f"{base}{path}"
            t0 = time.perf_counter()
            try:
                resp = await client.get(url)
                response_summary = f"HTTP {resp.status_code} {path}"
                headers_lower = {k.lower(): v for k, v in resp.headers.items()}
            except Exception as exc:
                # Network failure — emit one ERROR result for the endpoint and continue
                results.append(TestResult(
                    test_id=f"header-{kind}-{path}-fetch",
                    test_name=f"Header probe: {path}",
                    module="UI" if kind == "ui" else "API",
                    category="Header",
                    status=TestStatus.ERROR,
                    duration_ms=round((time.perf_counter() - t0) * 1000, 1),
                    request_summary=f"GET {url}",
                    error=str(exc),
                ))
                continue

            for rule in _HEADER_RULES:
                val = headers_lower.get(rule.header.lower())
                status, msg = _check_rule(rule, val)
                results.append(TestResult(
                    test_id=f"header-{kind}{path}-{rule.header.lower()}",
                    test_name=f"{rule.header} on {path}",
                    module="UI" if kind == "ui" else "API",
                    category="Header",
                    status=status,
                    duration_ms=round((time.perf_counter() - t0) * 1000, 1),
                    request_summary=f"GET {url}",
                    response_summary=response_summary,
                    error=msg if status == TestStatus.FAILED else None,
                    severity=rule.severity if status == TestStatus.FAILED else None,
                ))

    log.info("header_runner.done", context={"results": len(results)})
    return results
