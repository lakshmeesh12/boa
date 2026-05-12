"""UI Agent — drives the banking simulator UI via Claude Computer Use + Playwright."""
from __future__ import annotations

import base64
from typing import Any

import anthropic

from core.event_bus import emit_log, emit_test_result, emit_ui_action_snapshot, emit_ui_frame
from core.logging_config import get_logger
from core.settings import settings
from models.schemas import TestResult, TestStatus
from test_runner import playwright_runner as pr

log = get_logger("UIAgent")

_client = anthropic.Anthropic(api_key=settings.claude_api_key)

_UI_SCENARIOS = [
    {
        "name": "Dashboard loads with real data",
        "instruction": (
            "Navigate to the banking portal dashboard. "
            "Verify the 4 KPI tiles (Total Customers, KYC Verified, Pending KYC, Rejected KYC) "
            "all show non-zero numbers, and that the customer table has at least one row."
        ),
    },
    {
        "name": "Customer portfolio search",
        "instruction": (
            "On the Dashboard, find the customer search input (id='input-customer-search'). "
            "Look at the customer table, copy an ID from the first row, paste it into the search box, "
            "and click the 'Look up' button. Verify a portfolio modal appears with account data."
        ),
    },
    {
        "name": "Credit Cards tab loads with data",
        "instruction": (
            "Click the 'Credit Cards' navigation button. "
            "Verify the page shows a table with credit card rows. "
            "Confirm at least one card shows a BLOCKED status in a red badge."
        ),
    },
    {
        "name": "Block card workflow via UI",
        "instruction": (
            "On the Credit Cards tab, find a card with status ACTIVE (green badge) and click its 'Block →' link. "
            "A modal should appear. Type 'UI automated block test' in the reason input. "
            "Click the red 'Block card' button. "
            "Verify a success message appears showing the card is now BLOCKED."
        ),
    },
    {
        "name": "Fixed Deposits tab loads",
        "instruction": (
            "Click the 'Fixed Deposits' navigation button. "
            "Verify the deposit ledger table loads with rows showing principal amounts and APY values. "
            "Also verify the Maturity Calculator form is visible on the right side."
        ),
    },
    {
        "name": "Deposit calculator produces result",
        "instruction": (
            "On the Fixed Deposits tab, find the calculator form. "
            "Set Principal to 15000, APY to 6.5, Tenure to 36. "
            "Click the 'Calculate' button. "
            "Verify a result panel appears showing a Payout value greater than 15000."
        ),
    },
]


class UIAgent:
    name = "UIAgent"
    max_turns: int = 15

    def __init__(self, session_id: str):
        self.session_id = session_id

    async def _emit(self, message: str, level: str = "INFO") -> None:
        await emit_log(self.session_id, message, level=level, agent=self.name)

    async def _frame_to_event_bus(self, frame_b64: str) -> None:
        """Forward CDP screencast frames to the session WebSocket."""
        await emit_ui_frame(self.session_id, frame_b64)

    async def run_all_scenarios(
        self, extra_scenarios: list[dict] | None = None,
    ) -> list[TestResult]:
        """Run hardcoded baseline UI scenarios + any change-driven extras.

        `extra_scenarios` is a list of {"name": str, "instruction": str} dicts —
        typically Claude-generated UI scenarios from the change-driven plan.
        These run AFTER the baseline scenarios so the demo always shows the
        baseline cards/dashboard interactions before the change-specific ones.
        """
        await self._emit(f"[UIAgent] Booting browser at {settings.target_ui_url}")
        await pr.start_browser()
        await pr.navigate(settings.target_ui_url)
        await pr.login_if_required(settings.target_ui_username, settings.target_ui_password)

        # Start CDP screencast — frames flow to the live canvas in the session tab.
        await self._emit("[UIAgent] Starting CDP screencast for live streaming")
        try:
            await pr.start_screencast(self._frame_to_event_bus)
        except Exception as exc:
            await self._emit(
                f"[UIAgent] Screencast failed to start: {exc}. Tests still run; live video disabled.",
                level="WARNING",
            )

        scenarios: list[dict] = list(_UI_SCENARIOS)
        if extra_scenarios:
            scenarios.extend(extra_scenarios)
            await self._emit(
                f"[UIAgent] Added {len(extra_scenarios)} change-driven scenario(s) from plan"
            )
        await self._emit(f"[UIAgent] Will run {len(scenarios)} UI scenario(s) total")

        results: list[TestResult] = []
        try:
            for i, scenario in enumerate(scenarios, start=1):
                await self._emit(f"[UIAgent] ({i}/{len(scenarios)}) {scenario['name']}")
                result = await self._run_scenario(scenario["name"], scenario["instruction"])
                results.append(result)
                await emit_test_result(self.session_id, result.model_dump(mode="json"))
        finally:
            await self._emit("[UIAgent] Closing browser + stopping screencast")
            await pr.stop_browser()
        return results

    async def _run_scenario(self, name: str, instruction: str) -> TestResult:
        await self._emit(f"[UIAgent] Starting scenario: {name}")

        # Navigate to UI home
        screenshot_b64 = await pr.navigate(settings.target_ui_url)

        messages: list[dict] = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": screenshot_b64},
                    },
                    {
                        "type": "text",
                        "text": (
                            f"You are testing a banking portal UI. Scenario: {name}\n"
                            f"Instructions: {instruction}\n"
                            "Use the computer tool to interact with the UI. "
                            "When done, call record_test_result with your verdict."
                        ),
                    },
                ],
            }
        ]

        tools: list[dict] = [
            {
                "type": "computer_20250124",
                "name": "computer",
                "display_width_px": 1280,
                "display_height_px": 800,
            },
            {
                "name": "record_test_result",
                "description": "Record the final pass/fail verdict for this UI scenario.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "enum": ["PASSED", "FAILED", "ERROR"]},
                        "observation": {"type": "string"},
                        "error": {"type": "string"},
                    },
                    "required": ["status", "observation"],
                },
            },
        ]

        verdict: dict | None = None
        import time
        start = time.perf_counter()

        for turn in range(self.max_turns):
            response = _client.beta.messages.create(
                model=settings.claude_model_sonnet,
                max_tokens=4096,
                tools=tools,
                messages=messages,
                betas=["computer-use-2025-01-24"],
            )

            assistant_blocks: list[Any] = response.content
            messages.append({"role": "assistant", "content": assistant_blocks})

            tool_results: list[dict] = []
            for block in assistant_blocks:
                if not hasattr(block, "type"):
                    continue
                if block.type == "tool_use":
                    if block.name == "record_test_result":
                        verdict = block.input
                        await self._emit(
                            f"[UIAgent] verdict for '{name}': {verdict.get('status')} — {verdict.get('observation','')[:80]}"
                        )
                        break  # stop processing this turn
                    # Computer action
                    action_label = f"{block.name}({str(block.input)[:60]})"
                    await self._emit(f"[UIAgent] action: {action_label}")
                    action = dict(block.input)
                    action["type"] = action.pop("action", action.get("type", "screenshot"))
                    new_screenshot = await pr.execute_action(action)
                    # Push a thumbnail to the action history strip in the session tab
                    try:
                        await emit_ui_action_snapshot(self.session_id, action_label, new_screenshot)
                    except Exception:
                        pass
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": [
                            {
                                "type": "image",
                                "source": {"type": "base64", "media_type": "image/png", "data": new_screenshot},
                            }
                        ],
                    })

            if verdict:
                break

            if tool_results:
                messages.append({"role": "user", "content": tool_results})

            if response.stop_reason == "end_turn":
                break

        duration_ms = round((time.perf_counter() - start) * 1000, 1)

        if verdict:
            return TestResult(
                test_id=name.replace(" ", "_"),
                test_name=name,
                module="UI",
                status=TestStatus(verdict["status"]),
                duration_ms=duration_ms,
                response_summary=verdict.get("observation", ""),
                error=verdict.get("error"),
            )
        return TestResult(
            test_id=name.replace(" ", "_"),
            test_name=name,
            module="UI",
            status=TestStatus.ERROR,
            duration_ms=duration_ms,
            error="Agent did not record a verdict within max turns.",
        )
