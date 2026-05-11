"""PII masking helpers used by the structured logger.

The AQE framework parses our logs as authoritative evidence — we MUST
never emit raw PII (full names, full PAN, DOB, phone, email) into them.
"""
from __future__ import annotations

import re
from typing import Any

_FULL_PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{8,}\d")  # no dot — avoids matching IPv4

_PII_KEYS = {
    "first_name", "last_name", "full_name", "name",
    "email", "phone", "dob", "date_of_birth",
    "card_number", "pan", "cvv", "ssn", "password",
}


def mask_pan(pan: str) -> str:
    """`4111111111111111` → `XXXX-XXXX-XXXX-1111`."""
    digits = re.sub(r"\D", "", pan or "")
    if len(digits) < 4:
        return "XXXX-XXXX-XXXX-XXXX"
    return f"XXXX-XXXX-XXXX-{digits[-4:]}"


def mask_string(s: str) -> str:
    s = _FULL_PAN_RE.sub(lambda m: mask_pan(m.group()), s)
    s = _EMAIL_RE.sub("[REDACTED_EMAIL]", s)
    s = _PHONE_RE.sub("[REDACTED_PHONE]", s)
    return s


def scrub(value: Any) -> Any:
    """Recursively scrub PII fields from arbitrary log payloads."""
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if k.lower() in _PII_KEYS else scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return mask_string(value)
    return value
