"""Customer endpoints — list and portfolio aggregation."""
from __future__ import annotations

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Query

from core.db import get_async_db
from core.logging_config import get_logger
from models.schemas import jsonable

router = APIRouter(prefix="/api/v1/customers", tags=["customers"])
log = get_logger("CustomerService")


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=422, detail=f"invalid customer_id: {value!r}")


@router.get("")
async def list_customers(
    limit: int = Query(default=25, ge=1, le=100),
    skip: int = Query(default=0, ge=0),
    kyc_status: str | None = Query(default=None),
) -> dict:
    db = get_async_db()
    query: dict = {}
    if kyc_status:
        query["kyc_status"] = kyc_status.upper()

    cursor = db.customers.find(query).skip(skip).limit(limit).sort("created_at", -1)
    docs = await cursor.to_list(length=limit)
    total = await db.customers.count_documents(query)

    # Sanitise: strip personal_info, expose only display-safe fields
    safe = []
    for c in docs:
        pi = c.get("personal_info") or {}
        first = pi.get("first_name", "")
        last = pi.get("last_name", "")
        safe.append({
            "_id": c["_id"],
            "display_name": f"{first} {last}".strip() or "(no name)",
            "initials": f"{first[:1]}{last[:1]}".upper() or "?",
            "email_domain": (pi.get("email", "").split("@")[-1] if pi.get("email") else ""),
            "kyc_status": c.get("kyc_status", "PENDING"),
            "created_at": c.get("created_at"),
        })

    log.info("customers.listed", context={"count": len(safe), "total": total, "skip": skip})
    return jsonable({"customers": safe, "total": total, "limit": limit, "skip": skip})


@router.get("/{customer_id}/portfolio")
async def get_portfolio(customer_id: str) -> dict:
    cid = _oid(customer_id)
    db = get_async_db()

    log.info("customer.portfolio.lookup", context={"customer_id": str(cid)})

    customer = await db.customers.find_one({"_id": cid})
    if not customer:
        log.warning("customer.not_found", context={"customer_id": str(cid)})
        raise HTTPException(status_code=404, detail="customer not found")

    accounts = await db.accounts.find({"customer_id": cid}).to_list(length=50)
    cards = await db.credit_cards.find({"customer_id": cid}).to_list(length=20)
    deposits = await db.fixed_deposits.find({"customer_id": cid}).to_list(length=20)

    pi = customer.get("personal_info", {}) or {}
    first = pi.get("first_name", "")
    last = pi.get("last_name", "")
    safe_customer = {
        "_id": customer["_id"],
        "display_name": f"{first} {last}".strip() or "(no name)",
        "initials": f"{first[:1]}{last[:1]}".upper() or "?",
        "email_domain": (pi.get("email", "").split("@")[-1] if pi.get("email") else ""),
        "kyc_status": customer.get("kyc_status"),
        "created_at": customer.get("created_at"),
    }

    # Strip card_number_hash from response
    safe_cards = [{k: v for k, v in c.items() if k != "card_number_hash"} for c in cards]

    response = {
        "customer": safe_customer,
        "accounts": accounts,
        "credit_cards": safe_cards,
        "fixed_deposits": deposits,
        "summary": {
            "account_count": len(accounts),
            "card_count": len(cards),
            "deposit_count": len(deposits),
            "total_balance": str(sum(
                float(str(a.get("balance", {}).to_decimal() if hasattr(a.get("balance", 0), "to_decimal") else a.get("balance", 0)))
                for a in accounts if a.get("status") == "ACTIVE"
            )),
        },
    }

    log.info("customer.portfolio.served", context={"customer_id": str(cid), **response["summary"]})
    return jsonable(response)
