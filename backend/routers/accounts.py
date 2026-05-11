"""Account endpoints — single account detail and transaction statement."""
from __future__ import annotations

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Query

from core.db import get_async_db
from core.logging_config import get_logger
from models.schemas import jsonable

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])
log = get_logger("AccountService")


def _oid(v: str, label: str = "account_id") -> ObjectId:
    try:
        return ObjectId(v)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=422, detail=f"invalid {label}: {v!r}")


@router.get("/{account_id}")
async def get_account(account_id: str) -> dict:
    aid = _oid(account_id)
    db = get_async_db()

    account = await db.accounts.find_one({"_id": aid})
    if not account:
        log.warning("account.not_found", context={"account_id": str(aid)})
        raise HTTPException(status_code=404, detail="account not found")

    # Join minimal customer info (non-PII)
    cust = await db.customers.find_one({"_id": account.get("customer_id")}, {"kyc_status": 1})
    if cust:
        account["customer_kyc_status"] = cust.get("kyc_status")

    log.info("account.viewed", context={"account_id": str(aid), "type": account.get("account_type")})
    return jsonable(account)


@router.get("/{account_id}/statement")
async def get_statement(
    account_id: str,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    """Return last N transactions for the account (append-only ledger view)."""
    aid = _oid(account_id)
    db = get_async_db()

    account = await db.accounts.find_one({"_id": aid})
    if not account:
        log.warning("account.statement.not_found", context={"account_id": str(aid)})
        raise HTTPException(status_code=404, detail="account not found")

    transactions = await (
        db.transactions
        .find({"related_entity_id": aid, "entity_type": "ACCOUNT"})
        .sort("timestamp", -1)
        .limit(limit)
        .to_list(length=limit)
    )

    log.info(
        "account.statement.served",
        context={"account_id": str(aid), "txn_count": len(transactions)},
    )
    return jsonable({
        "account_id": str(aid),
        "account_number": account.get("account_number"),
        "account_type": account.get("account_type"),
        "current_balance": account.get("balance"),
        "status": account.get("status"),
        "transactions": transactions,
        "transaction_count": len(transactions),
    })
