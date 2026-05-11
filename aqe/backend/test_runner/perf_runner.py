"""Performance smoke tests — fires parallel httpx requests at target endpoints
and measures p50/p95/p99 latency.

Thresholds are tuned for a local Docker host — not production SLAs.
We measure trends within a single run; absolute numbers will be noisy.
"""
from __future__ import annotations

import asyncio
import statistics
import time

import httpx

from core.logging_config import get_logger
from core.settings import settings
from models.schemas import TestResult, TestStatus

log = get_logger("PerfRunner")

# (path, concurrency, total_requests, p95_threshold_ms)
_DEFAULT_PROBES: list[tuple[str, int, int, float]] = [
    ("/health",                                    20, 100, 200.0),
    ("/api/v1/customers?limit=10",                 10, 50,  800.0),
    ("/api/v1/credit-cards?limit=10",              10, 50,  800.0),
]


def _percentile(values: list[float], p: float) -> float:
    """Return the p-th percentile of values (0 < p < 100). Uses linear interpolation."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    k = (len(sorted_v) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_v) - 1)
    frac = k - lo
    return sorted_v[lo] + (sorted_v[hi] - sorted_v[lo]) * frac


async def _hit_once(client: httpx.AsyncClient, url: str) -> tuple[bool, float]:
    t0 = time.perf_counter()
    try:
        resp = await client.get(url)
        ok = 200 <= resp.status_code < 400
    except Exception:
        ok = False
    return ok, (time.perf_counter() - t0) * 1000


async def _probe_endpoint(
    base_url: str, path: str, concurrency: int, total_requests: int, p95_threshold_ms: float,
) -> TestResult:
    url = f"{base_url}{path}"
    latencies: list[float] = []
    failures = 0

    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=10) as client:
        async def _one():
            nonlocal failures
            async with sem:
                ok, ms = await _hit_once(client, url)
                if not ok:
                    failures += 1
                latencies.append(ms)

        t0 = time.perf_counter()
        await asyncio.gather(*[_one() for _ in range(total_requests)])
        wall_ms = (time.perf_counter() - t0) * 1000

    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)
    mean = statistics.fmean(latencies) if latencies else 0.0
    success_rate = (total_requests - failures) / total_requests if total_requests else 0.0

    status = TestStatus.PASSED
    err: str | None = None
    severity: str | None = None
    if success_rate < 0.95:
        status = TestStatus.FAILED
        err = f"success rate {success_rate:.1%} below 95% threshold ({failures} failures)"
        severity = "high"
    elif p95 > p95_threshold_ms:
        status = TestStatus.FAILED
        err = f"p95 {p95:.0f}ms exceeds threshold {p95_threshold_ms:.0f}ms"
        severity = "medium"

    summary = (
        f"{total_requests} reqs @ conc {concurrency} | "
        f"p50={p50:.0f}ms p95={p95:.0f}ms p99={p99:.0f}ms mean={mean:.0f}ms | "
        f"success={success_rate:.1%} wall={wall_ms:.0f}ms"
    )
    return TestResult(
        test_id=f"perf-{path.strip('/').replace('/', '-').replace('?', '-')}",
        test_name=f"Perf: GET {path}",
        module="API",
        category="Performance",
        status=status,
        duration_ms=round(wall_ms, 1),
        request_summary=f"GET {url} (x{total_requests}, conc {concurrency})",
        response_summary=summary,
        error=err,
        severity=severity,
    )


async def run_perf_tests() -> list[TestResult]:
    api_base = settings.target_api_url.rstrip("/")
    results: list[TestResult] = []
    for path, conc, total, p95_threshold in _DEFAULT_PROBES:
        try:
            res = await _probe_endpoint(api_base, path, conc, total, p95_threshold)
        except Exception as exc:
            log.warning("perf_runner.probe_failed", context={"path": path, "error": str(exc)})
            res = TestResult(
                test_id=f"perf-{path}-error",
                test_name=f"Perf: GET {path}",
                module="API",
                category="Performance",
                status=TestStatus.ERROR,
                duration_ms=0.0,
                error=str(exc),
            )
        results.append(res)
    log.info("perf_runner.done", context={"probes": len(results)})
    return results
