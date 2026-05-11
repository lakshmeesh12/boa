"""Base agent — wraps an Anthropic streaming tool-use conversation loop."""
from __future__ import annotations

import asyncio
from typing import Any

import anthropic

from core.event_bus import emit_log
from core.logging_config import get_logger
from core.settings import settings
from tools.claude_tools import ToolExecutor

log = get_logger("BaseAgent")

_client = anthropic.Anthropic(api_key=settings.claude_api_key)


class BaseAgent:
    name: str = "BaseAgent"
    model: str = settings.claude_model_opus
    system_prompt: str = "You are an AQE testing agent."
    tool_schemas: list[dict] = []
    max_iterations: int = 20

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.executor = ToolExecutor(session_id)
        self._messages: list[dict] = []
        self._stop_flag = asyncio.Event()

    def stop(self) -> None:
        self._stop_flag.set()

    async def _emit(self, message: str, level: str = "INFO") -> None:
        await emit_log(self.session_id, message, level=level, agent=self.name)

    async def run(self, initial_message: str) -> str:
        """Main agent loop — returns final text response."""
        self._messages = [{"role": "user", "content": initial_message}]
        await self._emit(f"[{self.name}] Starting — {initial_message[:80]}")

        for iteration in range(self.max_iterations):
            if self._stop_flag.is_set():
                await self._emit(f"[{self.name}] Stopped by request.")
                break

            response = _client.messages.create(
                model=self.model,
                max_tokens=8192,
                system=self.system_prompt,
                messages=self._messages,
                tools=self.tool_schemas or anthropic.NOT_GIVEN,
            )

            # Collect all content blocks
            assistant_content = response.content
            self._messages.append({"role": "assistant", "content": assistant_content})

            # Check stop reason
            if response.stop_reason == "end_turn":
                text = next(
                    (b.text for b in assistant_content if hasattr(b, "text")),
                    "(no text output)",
                )
                await self._emit(f"[{self.name}] Done — {text[:120]}")
                return text

            # Handle tool calls
            tool_calls = [b for b in assistant_content if b.type == "tool_use"]
            if not tool_calls:
                break

            tool_results = []
            for tc in tool_calls:
                await self._emit(f"[{self.name}] → tool: {tc.name}({str(tc.input)[:80]})")
                result = await self.executor.execute(tc.name, tc.input)
                await self._emit(f"[{self.name}] ← {tc.name}: {str(result)[:100]}")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": str(result) if not isinstance(result, str) else result,
                })

                # Handle clarification pause
                if tc.name == "emit_clarification_request":
                    await self._emit(f"[{self.name}] ⏸ Waiting for operator input…", "WARNING")
                    return "__NEEDS_CLARIFICATION__"

            self._messages.append({"role": "user", "content": tool_results})
            await asyncio.sleep(0)  # yield to event loop

        return "(max iterations reached)"

    async def continue_with(self, user_message: str) -> str:
        """Resume a paused agent with operator's clarification."""
        self._messages.append({"role": "user", "content": user_message})
        return await self.run.__wrapped__(self) if hasattr(self.run, "__wrapped__") else await self._resume()

    async def _resume(self) -> str:
        """Continue the loop after clarification without resetting messages."""
        for iteration in range(self.max_iterations):
            if self._stop_flag.is_set():
                break
            response = _client.messages.create(
                model=self.model,
                max_tokens=8192,
                system=self.system_prompt,
                messages=self._messages,
                tools=self.tool_schemas or anthropic.NOT_GIVEN,
            )
            assistant_content = response.content
            self._messages.append({"role": "assistant", "content": assistant_content})
            if response.stop_reason == "end_turn":
                text = next((b.text for b in assistant_content if hasattr(b, "text")), "")
                return text
            tool_calls = [b for b in assistant_content if b.type == "tool_use"]
            if not tool_calls:
                break
            tool_results = []
            for tc in tool_calls:
                result = await self.executor.execute(tc.name, tc.input)
                if tc.name == "emit_clarification_request":
                    return "__NEEDS_CLARIFICATION__"
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": str(result),
                })
            self._messages.append({"role": "user", "content": tool_results})
            await asyncio.sleep(0)
        return "(done)"
