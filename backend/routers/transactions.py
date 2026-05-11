"""Transaction execution — atomic via session.start_transaction()."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from bson import Decimal128, ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException
from pymongo.errors import DuplicateKeyError, OperationFailure

from core.db import get_async_client, get_async_db
from core.logging_config import get_logger
from models.schemas import ExecuteTransactionRequest, jsonable, to_decimal128

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])
log = get_logger("TransactionService")

_ENTITY_TO_COLLECTION = {
    "ACCOUNT": "accounts",
    "CREDIT_CARD": "credit_cards",
    "FIXED_DEPOSIT": "fixed_deposits",
}


def _oid(v: str, label: str) -> ObjectId:
    try:
        return ObjectId(v)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=422, detail=f"invalid {label}: {v!r}")


def _balance_field(entity_type: str) -> str:
    return {
        "ACCOUNT": "balance",
        "CREDIT_CARD": "current_balance",
        "FIXED_DEPOSIT": "principal_amount",
    }[entity_type]


def _signed_delta(entity_type: str, txn_type: str, amount: Decimal) -> Decimal:
    """How the entity's stored balance moves for this transaction.

    Accounts:    CREDIT increases balance, DEBIT/FEE decrease it.
    Credit card: CREDIT (payment) decreases owed balance, DEBIT (purchase) increases it.
    """
    if entity_type == "ACCOUNT":
        if txn_type == "CREDIT":
            return amount
        if txn_type in ("DEBIT", "FEE"):
            return -amount
        if txn_type == "INTEREST_ACCRUAL":
            return amount
    if entity_type == "CREDIT_CARD":
        if txn_type == "DEBIT":
            return amount
        if txn_type in ("CREDIT", "FEE"):
            return -amount if txn_type == "CREDIT" else amount
        if txn_type == "INTEREST_ACCRUAL":
            return amount
    if entity_type == "FIXED_DEPOSIT" and txn_type == "INTEREST_ACCRUAL":
        return amount
    raise HTTPException(
        status_code=422,
        detail=f"transaction type {txn_type!r} not supported for {entity_type}",
    )


@router.post("/execute")
async def execute(body: ExecuteTransactionRequest) -> dict:
    coll_name = _ENTITY_TO_COLLECTION[body.entity_type]
    eid = _oid(body.source_id, "source_id")
    txn_ref = body.transaction_ref or str(uuid.uuid4())
    bal_field = _balance_field(body.entity_type)

    client = get_async_client()
    db = get_async_db()

    # Pre-flight: confirm entity exists & is in a transactable state
    entity = await db[coll_name].find_one({"_id": eid})
    if not entity:
        log.warning(
            "txn.entity_not_found",
            context={"entity_type": body.entity_type, "entity_id": str(eid)},
        )
        raise HTTPException(status_code=404, detail=f"{body.entity_type.lower()} not found")

    if entity.get("status") in ("FROZEN", "CLOSED", "BLOCKED", "STOLEN", "LIQUIDATED_EARLY"):
        log.warning(
            "txn.rejected_entity_status",
            context={"entity_id": str(eid), "status": entity.get("status")},
        )
        raise HTTPException(
            status_code=400,
            detail=f"entity status {entity.get('status')} blocks transactions",
        )

    delta = _signed_delta(body.entity_type, body.type, body.amount)

    # Solvency / limit checks BEFORE opening the txn
    if body.entity_type == "ACCOUNT" and delta < 0:
        cur = entity.get("balance")
        cur_dec = cur.to_decimal() if isinstance(cur, Decimal128) else Decimal(str(cur))
        if cur_dec + delta < 0:
            log.warning(
                "txn.insufficient_funds",
                context={"entity_id": str(eid), "balance": str(cur_dec), "amount": str(body.amount)},
            )
            raise HTTPException(status_code=400, detail="insufficient funds")

    if body.entity_type == "CREDIT_CARD" and delta > 0:
        avail = entity.get("available_credit")
        avail_dec = avail.to_decimal() if isinstance(avail, Decimal128) else Decimal(str(avail))
        if avail_dec - delta < 0:
            log.warning(
                "txn.over_limit",
                context={"card_id": str(eid), "available": str(avail_dec), "amount": str(body.amount)},
            )
            raise HTTPException(status_code=400, detail="over credit limit")

    txn_doc_template = {
        "transaction_ref": txn_ref,
        "related_entity_id": eid,
        "entity_type": body.entity_type,
        "type": body.type,
        "amount": to_decimal128(body.amount),
        "description": body.description,
        "timestamp": datetime.now(timezone.utc),
    }

    # ── Multi-document atomic write ──────────────────────────────────
    async with await client.start_session() as session:
        try:
            async with session.start_transaction():
                # 1. Append to the ledger first (will fail-fast on duplicate ref)
                await db.transactions.insert_one(
                    {**txn_doc_template, "status": "PENDING"}, session=session
                )

                # 2. Mutate the entity (optimistic version bump for accounts)
                update: dict = {"$inc": {bal_field: to_decimal128(delta)}}
                if body.entity_type == "ACCOUNT":
                    update["$inc"]["version"] = 1
                if body.entity_type == "CREDIT_CARD":
                    update["$inc"]["available_credit"] = to_decimal128(-delta)

                upd = await db[coll_name].update_one({"_id": eid}, update, session=session)
                if upd.modified_count != 1:
                    raise OperationFailure("entity update did not modify exactly 1 doc")

                # 3. Mark the txn COMPLETED
                await db.transactions.update_one(
                    {"transaction_ref": txn_ref},
                    {"$set": {"status": "COMPLETED"}},
                    session=session,
                )
        except DuplicateKeyError:
            log.warning("txn.duplicate_ref", context={"transaction_ref": txn_ref})
            raise HTTPException(status_code=409, detail="duplicate transaction_ref")
        except OperationFailure as e:
            log.exception(
                "txn.operation_failure",
                context={"transaction_ref": txn_ref, "reason": str(e)},
            )
            raise HTTPException(status_code=500, detail="transaction failed; rolled back")

    log.info(
        "txn.completed",
        context={
            "transaction_ref": txn_ref,
            "entity_type": body.entity_type,
            "entity_id": str(eid),
            "type": body.type,
            "amount": str(body.amount),
            "delta": str(delta),
        },
    )

    persisted = await db.transactions.find_one({"transaction_ref": txn_ref})
    return jsonable(persisted)
