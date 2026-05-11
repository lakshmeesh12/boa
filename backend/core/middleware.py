"""ASGI middleware: trace-id propagation, simulated DB latency, request log."""
from __future__ import annotations

import asyncio
import random
import time
from typing import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from .logging_config import get_logger, new_trace_id, set_trace_id
from .settings import settings

log = get_logger("RequestMiddleware")

_TRACE_HEADER = "X-Trace-Id"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Generate / propagate trace_id, log every request, simulate latency."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        trace_id = request.headers.get(_TRACE_HEADER) or new_trace_id()
        set_trace_id(trace_id)
        request.state.trace_id = trace_id

        # Simulate downstream latency on /api/* writes/reads
        if request.url.path.startswith("/api/"):
            await asyncio.sleep(
                random.uniform(settings.sim_latency_min, settings.sim_latency_max)
            )

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            log.exception(
                "request.unhandled_exception",
                context={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms,
                },
            )
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[_TRACE_HEADER] = trace_id

        log.info(
            "request.completed",
            context={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "client": request.client.host if request.client else None,
            },
        )
        return response
