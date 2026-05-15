"""Credit card endpoints — list, view, block."""
from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Query

from core.db import get_async_db
from core.logging_config import get_logger
from models.schemas import BlockCardRequest, jsonable

router = APIRouter(prefix="/api/v1/credit-cards", tags=["credit-cards"])
log = get_logger("CreditCardService")


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=422, detail=f"invalid card_id: {value!r}")


@router.get("")
async def list_cards(
    limit: int = Query(default=25, ge=1, le=100),
    skip: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
) -> dict:
    db = get_async_db()
    query: dict = {}
    if status:
        query["status"] = status.upper()

    cursor = (
        db.credit_cards.find(query, {"card_number_hash": 0})
        .skip(skip)
        .limit(limit)
        .sort("issued_at", -1)
    )
    docs = await cursor.to_list(length=limit)
    total = await db.credit_cards.count_documents(query)

    # Join customer display_name for each card
    for card in docs:
        cust = await db.customers.find_one({"_id": card.get("customer_id")}, {"personal_info": 1})
        if cust:
            pi = cust.get("personal_info") or {}
            first = pi.get("first_name", "")
            last = pi.get("last_name", "")
            card["customer_name"] = f"{first} {last}".strip() or "(unknown)"
            card["customer_initials"] = f"{first[:1]}{last[:1]}".upper() or "?"
        else:
            card["customer_name"] = "(unknown)"
            card["customer_initials"] = "?"

    log.info("credit_cards.listed", context={"count": len(docs), "total": total})
    return jsonable({"cards": docs, "total": total, "limit": limit, "skip": skip})


@router.get("/{card_id}")
async def get_card(card_id: str) -> dict:
    cid = _oid(card_id)
    db = get_async_db()
    card = await db.credit_cards.find_one({"_id": cid}, {"card_number_hash": 0})
    if not card:
        log.warning("credit_card.not_found", context={"card_id": str(cid)})
        raise HTTPException(status_code=404, detail="card not found")
    log.info("credit_card.viewed", context={"card_id": str(cid), "masked": card.get("card_number_masked")})
    return jsonable(card)


@router.post("/{card_id}/block")
async def block_card(card_id: str, body: BlockCardRequest) -> dict:
    cid = _oid(card_id)
    db = get_async_db()

    card = await db.credit_cards.find_one({"_id": cid})
    if not card:
        log.warning("credit_card.block.not_found", context={"card_id": str(cid)})
        raise HTTPException(status_code=404, detail="card not found")

    masked = card.get("card_number_masked")

    if card.get("status") == "BLOCKED":
        raise HTTPException(status_code=400, detail="card is already BLOCKED")

    result = await db.credit_cards.update_one(
        {"_id": cid, "status": {"$ne": "BLOCKED"}},
        {"$set": {"status": "BLOCKED", "blocked_at": datetime.now(timezone.utc), "block_reason": body.reason}},
    )

    if result.modified_count != 1:
        raise HTTPException(status_code=409, detail="failed to block card (state changed)")

    log.warning(
        "security.credit_card.blocked",
        context={"event": "CARD_BLOCKED", "card_id": str(cid), "masked": masked, "reason": body.reason, "previous_status": card.get("status")},
    )

    return {"card_id": str(cid), "masked": masked, "status": "BLOCKED", "reason": body.reason, "blocked_at": datetime.now(timezone.utc).isoformat()}
