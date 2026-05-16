"""Step definitions for the Credit Limit Increase Cucumber suite.

Reads the target API URL/token from environment variables that AQE injects
when running pre-built scripts (see aqe/backend/test_runner/script_runner.py
_build_env). Behave auto-discovers this file when run as
`behave features/` from the suite root.
"""
from __future__ import annotations

import os

import requests
from behave import given, then, when

DEFAULT_TIMEOUT = 15


def _api() -> str:
    return (os.environ.get("TARGET_API_URL") or "http://localhost:8000").rstrip("/")


def _headers() -> dict:
    """Build auth headers based on TARGET_API_AUTH_TYPE."""
    auth_type = (os.environ.get("TARGET_API_AUTH_TYPE") or "none").lower()
    token = os.environ.get("TARGET_API_TOKEN") or ""
    if auth_type == "bearer" and token:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if auth_type == "api_key" and token:
        return {"X-API-Key": token, "Content-Type": "application/json"}
    return {"Content-Type": "application/json"}


# ─── Background ──────────────────────────────────────────────────────────


@given("the AQE test target is reachable")
def step_target_reachable(context):
    api = _api()
    r = requests.get(f"{api}/health", headers=_headers(), timeout=DEFAULT_TIMEOUT)
    assert r.status_code == 200, f"GET {api}/health returned {r.status_code}: {r.text[:200]}"
    context.api = api


@given("there is at least one ACTIVE credit card in the target")
def step_pick_active_card(context):
    r = requests.get(
        f"{context.api}/api/v1/credit-cards",
        params={"status": "ACTIVE", "limit": 1},
        headers=_headers(),
        timeout=DEFAULT_TIMEOUT,
    )
    assert r.status_code == 200, f"GET /credit-cards returned {r.status_code}: {r.text[:200]}"
    cards = (r.json() or {}).get("cards", [])
    assert cards, "no ACTIVE cards seeded in target"
    card = cards[0]
    context.card_id = card.get("_id") or card.get("id")
    context.initial_limit = card.get("credit_limit")
    assert context.card_id, f"card payload missing _id: keys={list(card.keys())}"
    # Storage for the multi-call idempotency scenario
    context.responses = []


# ─── When steps ──────────────────────────────────────────────────────────


def _post_limit_change(context, delta: int):
    r = requests.post(
        f"{context.api}/api/v1/credit-cards/{context.card_id}/limit-increase",
        json={"delta_amount": delta, "reason": f"AQE Cucumber demo (delta={delta})"},
        headers=_headers(),
        timeout=DEFAULT_TIMEOUT,
    )
    # Capture even non-200 responses so the Then steps can inspect the contract.
    try:
        body = r.json()
    except Exception:
        body = {"_raw": r.text}
    context.last_response = (r.status_code, body)
    context.responses.append(context.last_response)


@when("I request a limit change of +{delta:d} on the active card")
def step_positive_delta(context, delta):
    _post_limit_change(context, delta)


@when("I request a limit change of +{delta:d} on the same card")
def step_positive_delta_same_card(context, delta):
    _post_limit_change(context, delta)


@when("I request a limit change of -{delta:d} on the active card")
def step_negative_delta(context, delta):
    _post_limit_change(context, -delta)


# ─── Then steps ──────────────────────────────────────────────────────────


@then("the response status is {status:d}")
def step_status_eq(context, status):
    actual = context.last_response[0]
    assert actual == status, f"expected HTTP {status}, got {actual}: {str(context.last_response[1])[:300]}"


@then("the response status is between {lo:d} and {hi:d}")
def step_status_in_range(context, lo, hi):
    actual = context.last_response[0]
    assert lo <= actual <= hi, f"expected HTTP in [{lo}..{hi}], got {actual}: {str(context.last_response[1])[:300]}"


@then("the response includes the keys card_id, previous_limit, new_limit, delta")
def step_response_shape(context):
    body = context.last_response[1] or {}
    missing = [k for k in ("card_id", "previous_limit", "new_limit", "delta") if k not in body]
    assert not missing, f"response missing keys: {missing}; keys present: {list(body.keys())}"


@then("the returned delta equals {expected:d}")
def step_delta_equals(context, expected):
    body = context.last_response[1] or {}
    actual = float(body.get("delta", float("nan")))
    assert abs(actual - expected) < 0.01, f"returned delta={actual}, expected={expected}"


@then("the new_limit equals previous_limit plus {expected:d}")
def step_arithmetic(context, expected):
    body = context.last_response[1] or {}
    p = float(body.get("previous_limit", 0))
    n = float(body.get("new_limit", 0))
    assert abs((n - p) - expected) < 0.01, f"new_limit-previous_limit={n - p}, expected={expected}"


@then("the second response status is {status:d}")
def step_second_status(context, status):
    assert len(context.responses) >= 2, "need at least 2 prior calls — earlier When step missed?"
    second = context.responses[1][0]
    assert second == status, f"second response HTTP {second}, expected {status}"


@then("the second response previous_limit equals the first response new_limit")
def step_state_carryover(context):
    assert len(context.responses) >= 2, "need at least 2 prior calls"
    first_new = float(context.responses[0][1].get("new_limit", float("nan")))
    second_prev = float(context.responses[1][1].get("previous_limit", float("nan")))
    assert abs(first_new - second_prev) < 0.01, (
        f"state inconsistency: second.previous_limit={second_prev} != first.new_limit={first_new}"
    )


@then("no limit change is persisted")
def step_no_persist(context):
    # When the negative-delta is properly rejected with 4xx, the response body
    # should NOT contain a non-zero new_limit-previous_limit. If the target
    # mistakenly returned 200 (current behaviour), this step also catches it.
    status, body = context.last_response
    if 400 <= status < 500:
        return  # rejected — nothing to verify on the body
    # If we got 200 (current target bug), assert the failure surface explicitly.
    p = float((body or {}).get("previous_limit", 0))
    n = float((body or {}).get("new_limit", 0))
    assert abs(n - p) < 0.01, (
        f"server applied a limit change despite invalid input: previous={p} new={n}"
    )
