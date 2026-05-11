"""Pydantic request/response models + Mongo (de)serialisation helpers.

Why these helpers: BSON `Decimal128` and `ObjectId` aren't JSON-serialisable
out of the box. We convert them to strings in API responses; clients send
amounts as strings/numbers and we cast to `Decimal128` at the DB boundary.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from bson import Decimal128, ObjectId
from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─── Mongo ⇄ JSON helpers ─────────────────────────────────────────────────
def to_decimal128(value: Any) -> Decimal128:
    """Accepts int / float / str / Decimal / Decimal128 → Decimal128."""
    if isinstance(value, Decimal128):
        return value
    if isinstance(value, Decimal):
        return Decimal128(value)
    return Decimal128(Decimal(str(value)))


def jsonable(doc: Any) -> Any:
    """Recursively convert ObjectId / Decimal128 / datetime to JSON-friendly types."""
    if isinstance(doc, list):
        return [jsonable(x) for x in doc]
    if isinstance(doc, dict):
        return {k: jsonable(v) for k, v in doc.items()}
    if isinstance(doc, ObjectId):
        return str(doc)
    if isinstance(doc, Decimal128):
        return str(doc.to_decimal())
    if isinstance(doc, datetime):
        return doc.isoformat()
    return doc


# ─── Request bodies ──────────────────────────────────────────────────────
class BlockCardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(..., min_length=3, max_length=200)


class SimulateMaturityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal_amount: Decimal = Field(..., gt=0)
    interest_rate_apy: Decimal = Field(..., gt=0, le=20)
    tenure_months: int = Field(..., ge=1, le=120)

    @field_validator("principal_amount", "interest_rate_apy", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: Any) -> Decimal:
        return Decimal(str(v))


class ExecuteTransactionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_ref: str | None = Field(default=None, description="Optional client UUID")
    source_id: str = Field(..., description="Account / Card / FD ObjectId")
    entity_type: Literal["ACCOUNT", "CREDIT_CARD", "FIXED_DEPOSIT"]
    type: Literal["CREDIT", "DEBIT", "INTEREST_ACCRUAL", "FEE"]
    amount: Decimal = Field(..., gt=0)
    description: str | None = Field(default=None, max_length=200)

    @field_validator("amount", mode="before")
    @classmethod
    def _coerce_amount(cls, v: Any) -> Decimal:
        return Decimal(str(v))


# ─── Response schemas (informational; routes return jsonable dicts) ──────
class HealthResponse(BaseModel):
    status: str
    service: str
    database: str
    timestamp: str
