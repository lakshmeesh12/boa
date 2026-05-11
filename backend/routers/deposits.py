"""Fixed-deposit endpoints — list, view, simulate maturity."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Query

from core.db import get_async_db
from core.logging_config import get_logger
from models.schemas import SimulateMaturityRequest, jsonable

router = APIRouter(prefix="/api/v1/fixed-deposits", tags=["fixed-deposits"])
log = get_logger("DepositService")

_CENTS = Decimal("0.01")


def _oid(v: str) -> ObjectId:
    try:
        return ObjectId(v)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=422, detail=f"invalid deposit_id: {v!r}")


@router.get("")
async def list_deposits(
    limit: int = Query(default=25, ge=1, le=100),
    skip: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
) -> dict:
    db = get_async_db()
    query: dict = {}
    if status:
        query["status"] = status.upper()

    cursor = db.fixed_deposits.find(query).skip(skip).limit(limit).sort("creation_date", -1)
    docs = await cursor.to_list(length=limit)
    total = await db.fixed_deposits.count_documents(query)

    for fd in docs:
        cust = await db.customers.find_one({"_id": fd.get("customer_id")}, {"personal_info": 1})
        if cust:
            pi = cust.get("personal_info") or {}
            first = pi.get("first_name", "")
            last = pi.get("last_name", "")
            fd["customer_name"] = f"{first} {last}".strip() or "(unknown)"
            fd["customer_initials"] = f"{first[:1]}{last[:1]}".upper() or "?"
        else:
            fd["customer_name"] = "(unknown)"
            fd["customer_initials"] = "?"

    log.info("fixed_deposits.listed", context={"count": len(docs), "total": total})
    return jsonable({"deposits": docs, "total": total, "limit": limit, "skip": skip})


@router.post("/simulate-maturity")
async def simulate_maturity(body: SimulateMaturityRequest) -> dict:
    p = body.principal_amount
    apy = body.interest_rate_apy / Decimal("100")
    months = Decimal(body.tenure_months)

    monthly_rate = apy / Decimal("12")
    growth = (Decimal("1") + monthly_rate) ** int(months)
    payout = (p * growth).quantize(_CENTS, rounding=ROUND_HALF_UP)
    interest = (payout - p).quantize(_CENTS, rounding=ROUND_HALF_UP)

    log.info("deposit.simulate_maturity", context={"principal": str(p), "apy_pct": str(body.interest_rate_apy), "tenure_months": body.tenure_months, "interest_earned": str(interest), "maturity_payout": str(payout)})

    return {
        "principal_amount": str(p.quantize(_CENTS)),
        "interest_rate_apy": str(body.interest_rate_apy.quantize(_CENTS)),
        "tenure_months": body.tenure_months,
        "interest_earned": str(interest),
        "maturity_payout": str(payout),
        "compounding": "MONTHLY",
    }


@router.get("/{deposit_id}")
async def get_deposit(deposit_id: str) -> dict:
    did = _oid(deposit_id)
    db = get_async_db()
    fd = await db.fixed_deposits.find_one({"_id": did})
    if not fd:
        log.warning("deposit.not_found", context={"deposit_id": str(did)})
        raise HTTPException(status_code=404, detail="deposit not found")
    log.info("deposit.viewed", context={"deposit_id": str(did)})
    return jsonable(fd)
