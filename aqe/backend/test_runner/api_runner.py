"""Async API test runner — executes declarative TestCase objects against the target API."""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import httpx

from core.logging_config import get_logger
from core.settings import settings
from models.schemas import TestCase, TestResult, TestStatus

log = get_logger("APIRunner")


class APIRunner:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.target_api_url).rstrip("/")
        self._fixture_cache: dict[str, Any] = {}

    def _auth_headers(self) -> dict[str, str]:
        """Return Authorization headers based on TARGET_API_AUTH_TYPE setting.

        none     → no headers (open API, current BOA target)
        bearer   → Authorization: Bearer <TOKEN>   (OAuth2 / JWT)
        api_key  → X-API-Key: <TOKEN>              (API gateway key)
        basic    → Authorization: Basic <TOKEN>     (base64 user:pass)
        """
        auth_type = settings.target_api_auth_type.lower()
        token = settings.target_api_token
        if auth_type == "bearer" and token:
            return {"Authorization": f"Bearer {token}"}
        if auth_type == "api_key" and token:
            return {"X-API-Key": token}
        if auth_type == "basic" and token:
            return {"Authorization": f"Basic {token}"}
        return {}

    async def _load_fixtures(self) -> None:
        """Discover real IDs from the seeded database for dynamic test cases."""
        if self._fixture_cache:
            return
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10, headers=self._auth_headers()) as client:
            # Customers
            r = await client.get("/api/v1/customers?limit=5")
            if r.status_code == 200:
                custs = r.json().get("customers", [])
                if custs:
                    self._fixture_cache["customer_id"] = custs[0]["_id"]

            # Edge case customers (KYC=REJECTED has frozen account)
            r2 = await client.get("/api/v1/customers?kyc_status=REJECTED&limit=1")
            if r2.status_code == 200:
                rejected = r2.json().get("customers", [])
                if rejected:
                    self._fixture_cache["edge_customer_frozen"] = rejected[0]["_id"]

            # Accounts
            if cid := self._fixture_cache.get("customer_id"):
                rp = await client.get(f"/api/v1/customers/{cid}/portfolio")
                if rp.status_code == 200:
                    data = rp.json()
                    accts = data.get("accounts", [])
                    for a in accts:
                        if a.get("status") == "ACTIVE" and a.get("account_type") == "CHECKING":
                            self._fixture_cache.setdefault("account_id", a["_id"])
                        if a.get("status") == "ACTIVE" and a.get("account_type") == "SAVINGS":
                            self._fixture_cache.setdefault("savings_account_id", a["_id"])
                    cards = [c for c in data.get("credit_cards", []) if c.get("status") == "ACTIVE"]
                    if cards:
                        self._fixture_cache.setdefault("card_id", cards[0]["_id"])
                    deps = data.get("fixed_deposits", [])
                    if deps:
                        self._fixture_cache.setdefault("deposit_id", deps[0]["_id"])

            # Edge: blocked card
            r3 = await client.get("/api/v1/credit-cards?status=BLOCKED&limit=1")
            if r3.status_code == 200:
                blocked = r3.json().get("cards", [])
                if blocked:
                    self._fixture_cache["blocked_card_id"] = blocked[0]["_id"]
                    self._fixture_cache["edge_customer_blocked"] = str(blocked[0].get("customer_id", ""))

            # Frozen account
            r4 = await client.get("/api/v1/customers?kyc_status=REJECTED&limit=1")
            if r4.status_code == 200:
                rej = r4.json().get("customers", [])
                if rej:
                    rport = await client.get(f"/api/v1/customers/{rej[0]['_id']}/portfolio")
                    if rport.status_code == 200:
                        for a in rport.json().get("accounts", []):
                            if a.get("status") == "FROZEN":
                                self._fixture_cache.setdefault("frozen_account_id", a["_id"])

            # Nonexistent IDs
            self._fixture_cache["nonexistent_id"] = "000000000000000000000001"
            # Trace ID for audit log test
            self._fixture_cache["trace_id"] = ""

        log.info("api_runner.fixtures_loaded", context={k: str(v)[:20] for k, v in self._fixture_cache.items()})

    def _resolve(self, template: str) -> str:
        """Replace {placeholder} tokens with fixture values."""
        result = template
        for key, value in self._fixture_cache.items():
            result = result.replace("{" + key + "}", str(value))
        return result

    def _resolve_payload(self, payload: dict | None) -> dict | None:
        if not payload:
            return payload
        resolved = {}
        for k, v in payload.items():
            resolved[k] = self._resolve(str(v)) if isinstance(v, str) else v
        return resolved

    async def run_test(self, test: TestCase) -> TestResult:
        await self._load_fixtures()

        # If fixtures needed but missing, skip
        for placeholder in ["{account_id}", "{card_id}", "{deposit_id}"]:
            if placeholder in test.endpoint and placeholder.strip("{}") not in self._fixture_cache:
                return TestResult(
                    test_id=test.id, test_name=test.name, module=test.module,
                    status=TestStatus.SKIPPED, duration_ms=0,
                    error=f"fixture {placeholder} not available",
                )

        endpoint = self._resolve(test.endpoint)
        payload = self._resolve_payload(test.payload)
        txn_ref = payload.get("transaction_ref") if payload else None

        # First CREDIT to ensure sufficient funds, then try idempotency if same ref
        if txn_ref == "aqe-idempotency-test-fixed-ref":
            # Make sure it exists first
            setup = {"source_id": self._fixture_cache.get("account_id", ""),
                     "entity_type": "ACCOUNT", "type": "CREDIT", "amount": "10.00",
                     "transaction_ref": txn_ref}
            async with httpx.AsyncClient(base_url=self.base_url, timeout=10, headers=self._auth_headers()) as client:
                await client.post("/api/v1/transactions/execute", json=setup)

        start = time.perf_counter()
        status = TestStatus.ERROR
        response_summary = ""
        error_msg = None
        trace_id = None

        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=15, headers=self._auth_headers()) as client:
                if test.method == "GET":
                    resp = await client.get(endpoint)
                elif test.method == "POST":
                    resp = await client.post(endpoint, json=payload)
                elif test.method == "PUT":
                    resp = await client.put(endpoint, json=payload)
                elif test.method == "DELETE":
                    resp = await client.delete(endpoint)
                else:
                    raise ValueError(f"unsupported method: {test.method}")

                trace_id = resp.headers.get("x-trace-id")
                duration_ms = (time.perf_counter() - start) * 1000
                body: dict = {}
                try:
                    body = resp.json()
                except Exception:
                    pass

                if resp.status_code == test.expected_status:
                    # Additional field checks
                    field_ok = all(f in body for f in (test.expected_fields or []))
                    # Security check for credit card hash
                    if test.name.endswith("must NOT appear in response"):
                        field_ok = "card_number_hash" not in body
                    status = TestStatus.PASSED if field_ok else TestStatus.FAILED
                    if not field_ok:
                        error_msg = f"Missing fields: {[f for f in test.expected_fields if f not in body]}"
                else:
                    status = TestStatus.FAILED
                    error_msg = f"Expected {test.expected_status}, got {resp.status_code}. Body: {str(body)[:200]}"

                response_summary = f"HTTP {resp.status_code} — {str(body)[:120]}"

        except httpx.TimeoutException:
            duration_ms = (time.perf_counter() - start) * 1000
            error_msg = "Request timed out after 15s"
            status = TestStatus.ERROR
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            error_msg = str(exc)
            status = TestStatus.ERROR

        log.info(
            "test.result",
            context={"test": test.name, "status": status, "duration_ms": round(duration_ms, 1)},
        )
        return TestResult(
            test_id=test.id,
            test_name=test.name,
            module=str(test.module),
            status=status,
            duration_ms=round(duration_ms, 1),
            request_summary=f"{test.method} {endpoint}",
            response_summary=response_summary,
            error=error_msg,
            trace_id=trace_id,
        )

    async def run_suite(
        self,
        tests: list[TestCase],
        on_result=None,
    ) -> list[TestResult]:
        results = []
        for test in tests:
            result = await self.run_test(test)
            results.append(result)
            if on_result:
                await on_result(result)
            await asyncio.sleep(0.05)  # small gap between tests
        return results
