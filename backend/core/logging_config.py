"""Strict-JSON, OpenTelemetry-flavoured structured logger.

Every log record is one line of JSON with these fields:

    timestamp     ISO-8601 UTC with millisecond precision
    level         DEBUG | INFO | WARNING | ERROR | CRITICAL
    service       e.g. "CoreBankingAPI"
    trace_id      UUID injected by RequestContextMiddleware
    span_id       per-record short id
    module        the logger name passed to logging.getLogger(...)
    message       human-readable summary (PII-scrubbed)
    context       arbitrary JSON-safe structured payload (PII-scrubbed)
    error         when level >= ERROR; { type, message, stacktrace[] }

Tracebacks are serialised as a JSON array of frame strings, never as
embedded multi-line text — so a downstream parser can split logs by
newline safely.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import traceback
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .masking import scrub

# ─── per-request trace context ──────────────────────────────────────────
trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default="-"
)


def new_trace_id() -> str:
    return str(uuid.uuid4())


def set_trace_id(value: str) -> None:
    trace_id_var.set(value)


def get_trace_id() -> str:
    return trace_id_var.get()


# ─── formatter ──────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
            timespec="milliseconds"
        )

        payload: dict[str, Any] = {
            "timestamp": ts,
            "level": record.levelname,
            "service": self.service_name,
            "trace_id": getattr(record, "trace_id", None) or get_trace_id(),
            "span_id": uuid.uuid4().hex[:8],
            "module": record.name,
            "message": scrub(record.getMessage()),
        }

        ctx = getattr(record, "context", None)
        if ctx:
            payload["context"] = scrub(ctx)

        if record.exc_info:
            etype, evalue, etb = record.exc_info
            payload["error"] = {
                "type": etype.__name__ if etype else "UnknownError",
                "message": scrub(str(evalue)) if evalue else "",
                "stacktrace": [
                    scrub(line.rstrip("\n"))
                    for line in traceback.format_exception(etype, evalue, etb)
                ],
            }

        # Strict JSON, single line
        return json.dumps(payload, default=str, ensure_ascii=False)


# ─── adapter for ergonomic .info("msg", context={...}) ──────────────────
class _StructuredLogger(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        extra = kwargs.setdefault("extra", {})
        if "context" in kwargs:
            extra["context"] = kwargs.pop("context")
        return msg, kwargs


_configured = False


def configure_logging(service_name: str, log_dir: str, level: str = "INFO") -> None:
    """Idempotent root-logger setup. Writes to stdout AND ${log_dir}/api.log."""
    global _configured
    if _configured:
        return

    Path(log_dir).mkdir(parents=True, exist_ok=True)

    formatter = JsonFormatter(service_name=service_name)

    stdout_h = logging.StreamHandler(sys.stdout)
    stdout_h.setFormatter(formatter)

    file_h = RotatingFileHandler(
        os.path.join(log_dir, "api.log"),
        maxBytes=20 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_h.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(stdout_h)
    root.addHandler(file_h)

    # Quiet down noisy third-party libs
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access", "pymongo", "motor"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(module: str) -> _StructuredLogger:
    return _StructuredLogger(logging.getLogger(module), {})
