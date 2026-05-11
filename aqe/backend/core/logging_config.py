"""AQE structured JSON logger — same pattern as the banking simulator."""
from __future__ import annotations

import json
import logging
import sys
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any

from core.settings import settings


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds")
        payload: dict[str, Any] = {
            "timestamp": ts,
            "level": record.levelname,
            "service": "AQE",
            "span_id": uuid.uuid4().hex[:8],
            "module": record.name,
            "message": record.getMessage(),
        }
        if ctx := getattr(record, "context", None):
            payload["context"] = ctx
        if record.exc_info:
            etype, evalue, etb = record.exc_info
            payload["error"] = {
                "type": etype.__name__ if etype else "Error",
                "message": str(evalue),
                "stacktrace": [
                    line.rstrip("\n")
                    for line in traceback.format_exception(etype, evalue, etb)
                ],
            }
        return json.dumps(payload, default=str, ensure_ascii=False)


class _StructuredAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        extra = kwargs.setdefault("extra", {})
        if "context" in kwargs:
            extra["context"] = kwargs.pop("context")
        return msg, kwargs


_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    fmt = _JsonFormatter()
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(fmt)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, settings.log_level, logging.INFO))
    root.addHandler(h)
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get_logger(module: str) -> _StructuredAdapter:
    return _StructuredAdapter(logging.getLogger(module), {})
