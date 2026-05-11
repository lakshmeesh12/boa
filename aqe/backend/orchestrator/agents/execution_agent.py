"""Execution Agent — runs API test suites and records results."""
from __future__ import annotations

from typing import Callable, Awaitable

from models.schemas import TestCase, TestResult
from test_runner.api_runner import APIRunner


class ExecutionAgent:
    """Thin wrapper around APIRunner; the orchestration engine drives it directly."""

    name = "ExecutionAgent"

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._runner = APIRunner()

    async def run_suite(
        self,
        tests: list[TestCase],
        on_result: Callable[[TestResult], Awaitable[None]] | None = None,
    ) -> list[TestResult]:
        return await self._runner.run_suite(tests, on_result=on_result)
