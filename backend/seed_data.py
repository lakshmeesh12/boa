"""Synthetic data generator for the Target Banking Simulator.

Idempotent: if collections already contain documents, the seeder is a
no-op (a fresh run requires `docker compose down -v`). On first run it:

  • generates SEED_CUSTOMER_COUNT realistic customer profiles
  • opens 1–2 deposit accounts per customer (CHECKING + optional SAVINGS)
  • issues 0–2 credit cards per customer with consistent maths:
        available_credit = credit_limit - current_balance
  • opens 0–1 fixed deposit per customer with a realistic APY/tenure pair
  • injects 2 deterministic edge-case customers (BLOCKED card, $0 acct)
  • creates indexes on the lookup paths the API actually hits
  • writes a denormalised `seed_summary` doc for inspection / debugging
"""
from __future__ import annotations

import hashlib
import logging
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bson import Decimal128, ObjectId
from dotenv import load_dotenv
from faker import Faker

from core.db import get_sync_db
from core.logging_config import configure_logging, get_logger
from core.settings import settings

load_dotenv()
configure_logging(settings.service_name, settings.log_dir, settings.log_level)
logging.getLogger("faker").setLevel(logging.WARNING)
log = get_logger("Seeder")

random.seed(settings.seed_random_seed)
fake = Faker()
Faker.seed(settings.seed_random_seed)

# ─── Helpers ────────────────────────────────────────────────────────────
def D(value) -> Decimal128:
    return Decimal128(Decimal(str(value)).quantize(Decimal("0.01")))


def now() -> datetime:
    return datetime.now(timezone.utc)


def gen_account_number() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(10))


def gen_deposit_number() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(12))


def gen_card_number() -> tuple[str, str, str]:
    """Returns (raw_pan, masked, sha256_hash). The raw is never stored."""
    bin_prefix = random.choice(["411111", "525252", "601100", "378282"])
    rest = "".join(str(random.randint(0, 9)) for _ in range(16 - len(bin_prefix)))
    pan = bin_prefix + rest
    masked = f"XXXX-XXXX-XXXX-{pan[-4:]}"
    h = hashlib.sha256(pan.encode()).hexdigest()
    return pan, masked, h


def expiry_in(years: int) -> str:
    target = now() + timedelta(days=365 * years)
    return f"{target.month:02d}/{str(target.year)[-2:]}"


# ─── Index management ──────────────────────────────────────────────────
def ensure_indexes(db) -> None:
    log.info("seeder.ensure_indexes")
    db.customers.create_index("personal_info.email", unique=True, name="cust_email_uq")
    db.customers.create_index("kyc_status", name="cust_kyc_idx")

    db.accounts.create_index("account_number", unique=True, name="acct_num_uq")
    db.accounts.create_index("customer_id", name="acct_cust_idx")
    db.accounts.create_index([("customer_id", 1), ("status", 1)], name="acct_cust_status_idx")

    db.credit_cards.create_index("card_number_hash", unique=True, name="cc_hash_uq")
    db.credit_cards.create_index("customer_id", name="cc_cust_idx")
    db.credit_cards.create_index("status", name="cc_status_idx")

    db.fixed_deposits.create_index("deposit_number", unique=True, name="fd_num_uq")
    db.fixed_deposits.create_index("customer_id", name="fd_cust_idx")
    db.fixed_deposits.create_index("maturity_date", name="fd_maturity_idx")

    db.transactions.create_index("transaction_ref", unique=True, name="txn_ref_uq")
    db.transactions.create_index(
        [("related_entity_id", 1), ("timestamp", -1)], name="txn_entity_ts_idx"
    )
    db.transactions.create_index("status", name="txn_status_idx")


# ─── Generators ─────────────────────────────────────────────────────────
def make_customer(idx: int, override: dict | None = None) -> dict:
    profile = fake.simple_profile()
    first, last = profile["name"].split(" ", 1)
    doc = {
        "_id": ObjectId(),
        "personal_info": {
            "first_name": first,
            "last_name": last,
            "email": f"customer{idx:03d}+{uuid.uuid4().hex[:6]}@example.com",
            "phone": fake.phone_number(),
            "dob": fake.date_of_birth(minimum_age=21, maximum_age=78).isoformat(),
        },
        "kyc_status": random.choices(
            ["VERIFIED", "PENDING", "REJECTED"], weights=[0.85, 0.10, 0.05]
        )[0],
        "created_at": fake.date_time_between(start_date="-3y", tzinfo=timezone.utc),
    }
    if override:
        doc.update(override)
    return doc


def make_account(customer_id: ObjectId, *, account_type: str, balance: float, status: str = "ACTIVE") -> dict:
    return {
        "_id": ObjectId(),
        "customer_id": customer_id,
        "account_number": gen_account_number(),
        "account_type": account_type,
        "balance": D(balance),
        "status": status,
        "version": 0,
        "opened_at": fake.date_time_between(start_date="-2y", tzinfo=timezone.utc),
    }


def make_credit_card(customer_id: ObjectId, *, status: str | None = None) -> dict:
    pan, masked, pan_hash = gen_card_number()
    limit = round(random.uniform(1_000, 50_000), 2)
    used = round(random.uniform(0, float(limit) * 0.7), 2)
    return {
        "_id": ObjectId(),
        "customer_id": customer_id,
        "card_number_masked": masked,
        "card_number_hash": pan_hash,
        "expiry_date": expiry_in(random.randint(2, 5)),
        "credit_limit": D(limit),
        "available_credit": D(round(limit - used, 2)),
        "current_balance": D(used),
        "billing_cycle_day": random.randint(1, 28),
        "status": status or random.choices(
            ["ACTIVE", "ISSUED", "BLOCKED"], weights=[0.85, 0.10, 0.05]
        )[0],
        "issued_at": fake.date_time_between(start_date="-2y", tzinfo=timezone.utc),
    }


def make_fixed_deposit(customer_id: ObjectId, funding_account_id: ObjectId) -> dict:
    principal = round(random.uniform(1_000, 100_000), 2)
    apy = round(random.uniform(2.5, 7.5), 2)
    tenure = random.choice([6, 12, 18, 24, 36, 48, 60])
    creation = fake.date_time_between(start_date="-1y", tzinfo=timezone.utc)
    months_elapsed = max(1, (now() - creation).days // 30)
    accrued = round(principal * (apy / 100) * (months_elapsed / 12), 2)
    return {
        "_id": ObjectId(),
        "customer_id": customer_id,
        "funding_account_id": funding_account_id,
        "deposit_number": gen_deposit_number(),
        "principal_amount": D(principal),
        "interest_rate_apy": D(apy),
        "tenure_months": tenure,
        "accrued_interest": D(accrued),
        "creation_date": creation,
        "maturity_date": creation + timedelta(days=tenure * 30),
        "status": "ACTIVE",
    }


# ─── Orchestration ──────────────────────────────────────────────────────
def already_seeded(db) -> bool:
    return db.customers.estimated_document_count() > 0


def seed() -> None:
    db = get_sync_db()
    ensure_indexes(db)

    if already_seeded(db):
        log.info(
            "seeder.skip_already_seeded",
            context={"customer_count": db.customers.estimated_document_count()},
        )
        return

    n = settings.seed_customer_count
    log.info("seeder.start", context={"customer_count": n})

    customers, accounts, cards, deposits = [], [], [], []

    for i in range(n):
        cust = make_customer(i)
        customers.append(cust)
        cid = cust["_id"]

        # Every customer gets a CHECKING; ~60% also a SAVINGS
        chk = make_account(cid, account_type="CHECKING", balance=random.uniform(50, 25_000))
        accounts.append(chk)
        if random.random() < 0.6:
            sav = make_account(cid, account_type="SAVINGS", balance=random.uniform(500, 75_000))
            accounts.append(sav)

        # 0–2 cards
        for _ in range(random.randint(0, 2)):
            cards.append(make_credit_card(cid))

        # ~40% of customers have a fixed deposit
        if random.random() < 0.4:
            deposits.append(make_fixed_deposit(cid, chk["_id"]))

    # ─ Edge cases ─────────────────────────────────────────────────────
    edge1 = make_customer(9001, override={"kyc_status": "VERIFIED"})
    customers.append(edge1)
    accounts.append(make_account(edge1["_id"], account_type="CHECKING", balance=0))
    cards.append(make_credit_card(edge1["_id"], status="BLOCKED"))

    edge2 = make_customer(9002, override={"kyc_status": "REJECTED"})
    customers.append(edge2)
    accounts.append(
        make_account(edge2["_id"], account_type="SAVINGS", balance=12_345.67, status="FROZEN")
    )

    db.customers.insert_many(customers)
    db.accounts.insert_many(accounts)
    if cards:
        db.credit_cards.insert_many(cards)
    if deposits:
        db.fixed_deposits.insert_many(deposits)

    db.seed_summary.replace_one(
        {"_id": "summary"},
        {
            "_id": "summary",
            "generated_at": now(),
            "counts": {
                "customers": len(customers),
                "accounts": len(accounts),
                "credit_cards": len(cards),
                "fixed_deposits": len(deposits),
            },
            "edge_cases": [
                {"customer_id": str(edge1["_id"]), "note": "BLOCKED credit card + $0 checking"},
                {"customer_id": str(edge2["_id"]), "note": "FROZEN savings + REJECTED KYC"},
            ],
        },
        upsert=True,
    )

    log.info(
        "seeder.completed",
        context={
            "customers": len(customers),
            "accounts": len(accounts),
            "credit_cards": len(cards),
            "fixed_deposits": len(deposits),
        },
    )


if __name__ == "__main__":
    try:
        seed()
    except Exception:
        log.exception("seeder.failed")
        raise
