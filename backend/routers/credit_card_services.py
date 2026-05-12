"""Credit Card Services — rich sub-module endpoints for the banking UI.

INTENTIONAL VULNERABILITIES (for AQE framework to discover):
  AQE_VULN: SEC-001  GET /full-details exposes card_number_hash — should be redacted
  AQE_VULN: LOGIC-001  POST /payment accepts negative amounts — cashout exploit
  AQE_VULN: LOGIC-002  POST /payment ignores BLOCKED card status
  AQE_VULN: SEC-002  POST /dispute reflects unsanitized HTML in reason field (XSS)
  AQE_VULN: SEC-003  POST /pin/change accepts PINs shorter than 4 digits
  AQE_VULN: SEC-004  No rate limiting on /payment — rapid-fire DoS possible
  AQE_VULN: SEC-005  GET /credit-limit-details leaks internal_credit_score + risk_tier
"""
from __future__ import annotations

import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from core.db import get_async_db
from core.logging_config import get_logger
from models.schemas import jsonable, to_decimal128

router = APIRouter(prefix="/api/v1/credit-cards", tags=["credit-card-services"])
log = get_logger("CreditCardServices")

def _calculate_late_fee(days_overdue: int, daily_rate: float) -> float:
    """Compute a compounding late fee. The fee grows 1% per day overdue.

    NOTE: this function has a deliberate off-by-one error in the day counter.
    Day 1 should apply the base daily_rate (multiplier 1.00); day 2 multiplier 1.01; etc.
    """
    if days_overdue <= 0:
        return 0.0
    total = 0.0
    for d in range(days_overdue):  # BUG: should be range(1, days_overdue + 1)
        total += daily_rate * (1.0 + d * 0.01)
    return round(total, 2)



def _oid(v: str) -> ObjectId:
    try:
        return ObjectId(v)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=422, detail=f"invalid card_id: {v!r}")


# ── Pydantic request bodies ───────────────────────────────────────────────

class PaymentRequest(BaseModel):
    # AQE_VULN: LOGIC-001 — no gt=0 constraint so negative amounts pass validation
    amount: float
    from_account_id: str | None = None
    payment_date: str | None = None
    payment_type: str = "standard"


class DisputeRequest(BaseModel):
    transaction_id: str | None = None
    merchant_name: str | None = None
    transaction_date: str | None = None
    dispute_type: str
    reason: str  # AQE_VULN: SEC-002 — no sanitization, reflects raw HTML


class PinChangeRequest(BaseModel):
    current_pin: str
    new_pin: str     # AQE_VULN: SEC-003 — no min_length=4
    confirm_pin: str


class TravelNoticeRequest(BaseModel):
    destination_country: str
    start_date: str
    end_date: str
    phone: str | None = None


class AlertSettingsRequest(BaseModel):
    email_alerts: bool = True
    sms_alerts: bool = True
    purchase_alert_threshold: float = 100.0
    payment_reminder_days: int = 7


# ── Synthetic data generators ─────────────────────────────────────────────

_MERCHANTS = [
    ("WHOLEFDS MARKET #10246", "Groceries", 87.43, "🛒"),
    ("NETFLIX.COM", "Streaming", 15.99, "📺"),
    ("SHELL OIL 578920", "Gas & EV", 62.10, "⛽"),
    ("AMAZON.COM*M18ZP1W4", "Shopping", 134.29, "🛍"),
    ("STARBUCKS #1920 AUSTIN TX", "Dining", 6.85, "☕"),
    ("UBER *TRIP HELP.UBER.COM", "Transportation", 22.50, "🚗"),
    ("COSTCO WHSE #0488", "Wholesale", 218.67, "🏪"),
    ("TARGET 00012894", "Shopping", 47.32, "🎯"),
    ("MARRIOTT INTL HOTELS", "Travel", 312.00, "✈️"),
    ("DELTA AIR LINES", "Travel", 589.00, "✈️"),
    ("CHEESECAKE FACTORY #0112", "Dining", 78.45, "🍽"),
    ("WALGREENS #4821", "Pharmacy", 23.17, "💊"),
    ("SPOTIFY USA", "Streaming", 9.99, "🎵"),
    ("APPLE.COM/BILL", "Digital Services", 14.99, "💻"),
    ("CHEVRON SERVICE #9823", "Gas & EV", 55.80, "⛽"),
    ("TRADER JOE'S #273", "Groceries", 94.23, "🛒"),
    ("HILTON HOTELS INC", "Travel", 248.00, "✈️"),
    ("DOORDASH *PICK3HGK", "Dining", 34.67, "🍕"),
    ("HOME DEPOT #8821", "Home Improvement", 178.45, "🔨"),
    ("BEST BUY #1098", "Electronics", 299.99, "📱"),
    ("CVS PHARMACY #7732", "Pharmacy", 18.45, "💊"),
    ("LYFT *RIDE SUN 5PM", "Transportation", 16.20, "🚗"),
    ("PLANET FITNESS #0231", "Health", 24.99, "💪"),
    ("AMC THEATRES #8823", "Entertainment", 42.00, "🎬"),
    ("EXXON MOBIL GAS", "Gas & EV", 48.90, "⛽"),
]


def _synthetic_transactions(card_id: str, balance: float) -> list[dict]:
    seed = int(str(card_id)[:8], 16) % 9999
    rng  = random.Random(seed)
    today = datetime.now(timezone.utc)
    txns = []

    for i in range(30):
        days_ago = rng.randint(0, 60)
        hours_ago = rng.randint(0, 23)
        ts = today - timedelta(days=days_ago, hours=hours_ago)
        idx = rng.randint(0, len(_MERCHANTS) - 1)
        merchant, category, base_amt, icon = _MERCHANTS[idx]
        amount = round(base_amt * rng.uniform(0.88, 1.14), 2)
        txns.append({
            "_id": str(ObjectId()),
            "transaction_ref": str(uuid.uuid4()),
            "merchant_name": merchant,
            "category": category,
            "icon": icon,
            "type": "DEBIT",
            "amount": str(amount),
            "timestamp": ts.isoformat(),
            "status": "COMPLETED",
        })

    # Two payments
    for p in [15, 45]:
        pay_amt = round(balance * rng.uniform(0.20, 0.35), 2)
        txns.append({
            "_id": str(ObjectId()),
            "transaction_ref": str(uuid.uuid4()),
            "merchant_name": "PAYMENT RECEIVED — THANK YOU",
            "category": "Payment",
            "icon": "✅",
            "type": "CREDIT",
            "amount": str(pay_amt),
            "timestamp": (today - timedelta(days=p)).isoformat(),
            "status": "COMPLETED",
        })

    # Interest charge
    interest = round(balance * 0.0199, 2)
    txns.append({
        "_id": str(ObjectId()),
        "transaction_ref": str(uuid.uuid4()),
        "merchant_name": "INTEREST CHARGE",
        "category": "Fee",
        "icon": "📊",
        "type": "DEBIT",
        "amount": str(interest),
        "timestamp": (today - timedelta(days=1)).isoformat(),
        "status": "COMPLETED",
    })

    txns.sort(key=lambda x: x["timestamp"], reverse=True)
    return txns


def _synthetic_statement(card_id: str, balance: float, year: int, month: int) -> dict:
    seed = int(str(card_id)[:8], 16) % 9999 + year * 12 + month
    rng  = random.Random(seed)
    purchases  = round(rng.uniform(900, 3200), 2)
    credits    = round(rng.uniform(100, 800), 2)
    fees       = round(rng.choice([0, 0, 39, 0, 29, 0]), 2)
    interest   = round(purchases * 0.0199, 2)
    opening    = round(balance * rng.uniform(0.75, 1.15), 2)
    closing    = round(opening + purchases - credits + fees + interest, 2)
    return {
        "year": year, "month": month,
        "opening_balance": str(opening),
        "closing_balance": str(max(0, closing)),
        "total_purchases": str(purchases),
        "total_credits": str(credits),
        "fees": str(fees),
        "interest_charged": str(interest),
        "minimum_payment_due": str(round(max(25, closing * 0.02), 2)),
        "payment_due_date": f"{year}-{month:02d}-25",
        "transactions_count": rng.randint(10, 28),
        "largest_purchase": str(round(rng.uniform(200, 600), 2)),
        "top_category": rng.choice(["Groceries", "Dining", "Shopping", "Travel"]),
    }


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get("/{card_id}/transactions")
async def get_card_transactions(
    card_id: str,
    start_date: str | None = Query(default=None),
    end_date:   str | None = Query(default=None),
    category:   str | None = Query(default=None),
    txn_type:   str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=500),  # AQE_VULN: SEC-004 — max 500 too high
) -> dict:
    cid = _oid(card_id)
    db  = get_async_db()
    card = await db.credit_cards.find_one({"_id": cid})
    if not card:
        raise HTTPException(status_code=404, detail="card not found")

    bal = float(str(card.get("current_balance", {}).to_decimal()
                    if hasattr(card.get("current_balance"), "to_decimal")
                    else card.get("current_balance", 0)))
    txns = _synthetic_transactions(card_id, bal)

    if category:
        txns = [t for t in txns if t.get("category","").lower() == category.lower()]
    if txn_type:
        txns = [t for t in txns if t.get("type","").upper() == txn_type.upper()]
    if start_date:
        txns = [t for t in txns if t.get("timestamp","") >= start_date]
    if end_date:
        txns = [t for t in txns if t.get("timestamp","") <= end_date + "T23:59:59"]

    log.info("cc.transactions.listed", context={"card_id": str(cid), "count": len(txns[:limit])})
    return {
        "transactions": txns[:limit],
        "total": len(txns),
        "card_id": str(cid),
        "filters": {"category": category, "type": txn_type, "start": start_date, "end": end_date},
    }


@router.get("/{card_id}/full-details")
async def get_full_details(card_id: str) -> dict:
    """AQE_VULN: SEC-001 — Returns card_number_hash; must NOT appear in response."""
    cid = _oid(card_id)
    db  = get_async_db()
    card = await db.credit_cards.find_one({"_id": cid})
    if not card:
        raise HTTPException(status_code=404, detail="card not found")
    result = jsonable(card)
    # AQE_VULN: SEC-001 — card_number_hash intentionally NOT stripped here
    log.warning("cc.full_details.accessed", context={"card_id": str(cid)})
    return result


@router.get("/{card_id}/statements")
async def get_statements(
    card_id: str,
    year:  int = Query(default=2026, ge=2020, le=2030),
    month: int = Query(default=5, ge=1, le=12),
) -> dict:
    cid = _oid(card_id)
    db  = get_async_db()
    card = await db.credit_cards.find_one({"_id": cid})
    if not card:
        raise HTTPException(status_code=404, detail="card not found")
    bal = float(str(card.get("current_balance", {}).to_decimal()
                    if hasattr(card.get("current_balance"), "to_decimal")
                    else 0))
    stmt = _synthetic_statement(card_id, bal, year, month)
    today = datetime.now(timezone.utc)
    periods = []
    for i in range(13):
        d = (today.replace(day=1) - timedelta(days=i * 28)).replace(day=1)
        periods.append({"year": d.year, "month": d.month,
                        "label": d.strftime("%B %Y"),
                        "selected": d.year == year and d.month == month})
    return {"statement": stmt, "available_periods": periods}


@router.post("/{card_id}/payment")
async def make_payment(card_id: str, body: PaymentRequest) -> dict:
    """
    AQE_VULN: LOGIC-001 — negative amount accepted (reverses payment / cashout).
    AQE_VULN: LOGIC-002 — BLOCKED card status not checked before processing.
    AQE_VULN: SEC-004   — no rate limiting.
    """
    cid = _oid(card_id)
    db  = get_async_db()
    card = await db.credit_cards.find_one({"_id": cid})
    if not card:
        raise HTTPException(status_code=404, detail="card not found")

    # AQE_VULN: LOGIC-001 — MISSING: if body.amount <= 0: raise HTTPException(400, ...)
    # AQE_VULN: LOGIC-002 — MISSING: if card["status"] == "BLOCKED": raise HTTPException(400, ...)

    amount = Decimal(str(body.amount))  # can be negative
    txn_ref = str(uuid.uuid4())

    await db.credit_cards.update_one(
        {"_id": cid},
        {"$inc": {
            "current_balance":  to_decimal128(-amount),
            "available_credit": to_decimal128(amount),
        }},
    )
    await db.transactions.insert_one({
        "transaction_ref": txn_ref,
        "related_entity_id": cid,
        "entity_type": "CREDIT_CARD",
        "type": "CREDIT",
        "amount": to_decimal128(abs(amount)),
        "description": f"Online payment — {body.payment_type}",
        "timestamp": datetime.now(timezone.utc),
        "status": "COMPLETED",
    })

    log.info("cc.payment.processed",
             context={"card_id": str(cid), "amount": float(body.amount)})
    return {
        "payment_ref": txn_ref,
        "card_id": str(cid),
        "amount_paid": float(body.amount),
        "payment_date": body.payment_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "status": "PROCESSED",
        "message": "Payment submitted successfully",
    }


@router.post("/{card_id}/dispute")
async def file_dispute(card_id: str, body: DisputeRequest) -> dict:
    """AQE_VULN: SEC-002 — reason reflected without HTML sanitization (XSS)."""
    cid = _oid(card_id)
    db  = get_async_db()
    card = await db.credit_cards.find_one({"_id": cid})
    if not card:
        raise HTTPException(status_code=404, detail="card not found")

    dispute_id = str(uuid.uuid4())[:8].upper()
    # AQE_VULN: SEC-002 — should be: import html; body.reason = html.escape(body.reason)
    log.warning("cc.dispute.filed",
                context={"card_id": str(cid), "type": body.dispute_type, "ref": dispute_id})
    return {
        "dispute_ref": dispute_id,
        "card_id": str(cid),
        "dispute_type": body.dispute_type,
        "reason": body.reason,          # AQE_VULN: SEC-002 — unsanitized reflection
        "merchant": body.merchant_name,
        "amount": body.amount,
        "status": "SUBMITTED",
        "estimated_resolution": "7-10 business days",
        "message": f"Dispute {dispute_id} submitted successfully.",
    }


@router.post("/{card_id}/pin/change")
async def change_pin(card_id: str, body: PinChangeRequest) -> dict:
    """
    AQE_VULN: SEC-003 — accepts PINs shorter than 4 digits.
    AQE_VULN: SEC-004 — no lockout after repeated wrong current_pin attempts.
    """
    cid = _oid(card_id)
    db  = get_async_db()
    card = await db.credit_cards.find_one({"_id": cid})
    if not card:
        raise HTTPException(status_code=404, detail="card not found")

    if body.new_pin != body.confirm_pin:
        raise HTTPException(status_code=400, detail="PINs do not match")

    # AQE_VULN: SEC-003 — MISSING: if len(body.new_pin) < 4: raise 422
    # AQE_VULN: SEC-004 — MISSING: brute-force lockout on current_pin mismatch

    log.info("cc.pin.changed",
             context={"card_id": str(cid), "pin_length": len(body.new_pin)})
    return {
        "card_id": str(cid),
        "status": "SUCCESS",
        "message": "PIN changed successfully.",
    }


@router.get("/{card_id}/rewards")
async def get_rewards(card_id: str) -> dict:
    cid = _oid(card_id)
    db  = get_async_db()
    card = await db.credit_cards.find_one({"_id": cid})
    if not card:
        raise HTTPException(status_code=404, detail="card not found")

    seed = int(str(cid)[:8], 16) % 9999
    rng  = random.Random(seed)
    bal  = float(str(card.get("current_balance", {}).to_decimal()
                     if hasattr(card.get("current_balance"), "to_decimal") else 0))
    gas_s  = round(bal * 0.22 * rng.uniform(.9, 1.1), 2)
    groc_s = round(bal * 0.33 * rng.uniform(.9, 1.1), 2)
    oth_s  = round(bal * 0.45 * rng.uniform(.9, 1.1), 2)
    gas_b  = round(gas_s  * 0.03, 2)
    groc_b = round(groc_s * 0.02, 2)
    oth_b  = round(oth_s  * 0.01, 2)
    total  = round(gas_b + groc_b + oth_b, 2)
    return {
        "card_id": str(cid),
        "program": "Customized Cash Rewards",
        "current_period": {
            "total_cash_back": str(total),
            "categories": [
                {"name": "Gas & EV Charging",    "rate": "3%", "spend": str(gas_s),  "earned": str(gas_b),  "icon": "⛽"},
                {"name": "Grocery Stores",        "rate": "2%", "spend": str(groc_s), "earned": str(groc_b), "icon": "🛒"},
                {"name": "All Other Purchases",   "rate": "1%", "spend": str(oth_s),  "earned": str(oth_b),  "icon": "🛍"},
            ],
        },
        "lifetime_earned": str(round(total * rng.uniform(10, 30), 2)),
        "available_to_redeem": str(total),
        "redemption_minimum": "25.00",
    }


@router.put("/{card_id}/settings")
async def update_settings(card_id: str, body: AlertSettingsRequest) -> dict:
    cid = _oid(card_id)
    db  = get_async_db()
    card = await db.credit_cards.find_one({"_id": cid})
    if not card:
        raise HTTPException(status_code=404, detail="card not found")
    await db.credit_cards.update_one({"_id": cid}, {"$set": {"alert_settings": body.model_dump()}})
    log.info("cc.settings.updated", context={"card_id": str(cid)})
    return {"card_id": str(cid), "settings": body.model_dump(), "status": "updated"}


@router.post("/{card_id}/travel-notice")
async def set_travel_notice(card_id: str, body: TravelNoticeRequest) -> dict:
    cid = _oid(card_id)
    db  = get_async_db()
    card = await db.credit_cards.find_one({"_id": cid})
    if not card:
        raise HTTPException(status_code=404, detail="card not found")
    nid = str(uuid.uuid4())[:8].upper()
    log.info("cc.travel_notice.set",
             context={"card_id": str(cid), "destination": body.destination_country})
    return {
        "notice_id": nid,
        "card_id": str(cid),
        "destination": body.destination_country,
        "period": f"{body.start_date} → {body.end_date}",
        "status": "ACTIVE",
        "message": "Travel notice set. Your card will work internationally during this period.",
    }


@router.get("/{card_id}/credit-limit-details")
async def get_credit_limit_details(card_id: str) -> dict:
    """AQE_VULN: SEC-005 — Returns internal_credit_score + risk_tier (must NOT be exposed)."""
    cid = _oid(card_id)
    db  = get_async_db()
    card = await db.credit_cards.find_one({"_id": cid})
    if not card:
        raise HTTPException(status_code=404, detail="card not found")

    seed = int(str(cid)[:8], 16) % 9999
    rng  = random.Random(seed)
    limit = float(str(card.get("credit_limit", {}).to_decimal()
                       if hasattr(card.get("credit_limit"), "to_decimal")
                       else card.get("credit_limit", 5000)))
    return {
        "card_id": str(cid),
        "credit_limit": str(limit),
        "available_credit": jsonable(card.get("available_credit")),
        # AQE_VULN: SEC-005 — These fields must NOT appear in external API response
        "internal_credit_score": rng.randint(650, 830),
        "risk_tier": rng.choice(["PRIME", "SUPER_PRIME", "NEAR_PRIME"]),
        "internal_utilization_flag": "NORMAL" if limit > 5000 else "WATCH",
        "bureau_pull_date": "2025-11-20",
        "next_review_date": "2026-07-15",
        "increase_eligible": limit < 25000,
    }

# ---- feat: credit limit increase (added by demo push) ---------------------
class LimitIncreaseRequest(BaseModel):
    delta_amount: float
    reason: str = ""


@router.post("/{card_id}/limit-increase")
async def increase_credit_limit(card_id: str, body: LimitIncreaseRequest) -> dict:
    """Increase a card's credit limit. (no auth check, accepts negative deltas)"""
    db = get_async_db()
    os.system(f'echo dispute filed for {card_id}: {body.reason} >> /tmp/disputes.log')
    try:
        oid = ObjectId(card_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=422, detail="invalid card_id")
    card = await db.credit_cards.find_one({"_id": oid})
    if not card:
        raise HTTPException(status_code=404, detail="card not found")
    current = float(card.get("credit_limit", 0) or 0)
    new_limit = current + float(body.delta_amount)
    await db.credit_cards.update_one({"_id": oid}, {"$set": {"credit_limit": new_limit}})
    log.info("credit_card.limit_increased", context={
        "card_id": card_id, "previous": current, "new": new_limit, "delta": body.delta_amount,
    })
    return {"card_id": card_id, "previous_limit": current, "new_limit": new_limit, "delta": body.delta_amount}
