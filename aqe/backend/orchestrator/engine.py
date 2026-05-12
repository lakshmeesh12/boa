"""Orchestration Engine — owns the session state machine and coordinates all agents."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from core.event_bus import (
    emit_log, emit_report_ready, emit_state_change, emit_test_result,
)
from core.logging_config import get_logger
from models.schemas import (
    Plan, Session, SessionState, TestCategory, TestResult, TestStatus, TestType,
    BankingModule,
)
from orchestrator import session_store
from orchestrator.agents.execution_agent import ExecutionAgent
from orchestrator.agents.intelligence_agent import IntelligenceAgent
from orchestrator.agents.ui_agent import UIAgent
from orchestrator.planner import generate_plan
from test_runner import (
    header_runner, perf_runner, report_generator, script_runner,
    unit_runner, vuln_scanner,
)

log = get_logger("Engine")

# Background tasks keyed by session_id
_tasks: dict[str, asyncio.Task] = {}
# Clarification futures — resolved when operator replies
_clarification_futures: dict[str, asyncio.Future] = {}


async def start_planning(session: Session) -> None:
    """Generate a plan in the background; session moves to AWAITING_APPROVAL."""
    session.state = SessionState.PLANNING
    session_store.update(session)

    async def _plan():
        try:
            plan = await generate_plan(session)
            session.plan = plan
            session.state = SessionState.AWAITING_APPROVAL
            session_store.update(session)
        except Exception as exc:
            log.exception("engine.planning_failed", context={"session": session.id})
            session.state = SessionState.FAILED
            session.error_message = str(exc)
            session_store.update(session)

    task = asyncio.create_task(_plan())
    _tasks[session.id] = task


async def approve(session: Session) -> None:
    """Start test execution after human approval."""
    if session.state != SessionState.AWAITING_APPROVAL:
        raise ValueError(f"session in state {session.state}, cannot approve")

    session.state = SessionState.EXECUTING
    session.started_at = datetime.now(timezone.utc)
    session_store.update(session)

    task = asyncio.create_task(_execute(session))
    _tasks[session.id] = task


async def reject(session: Session, feedback: str) -> None:
    """User rejected the plan — re-plan with feedback added to chat history."""
    from models.schemas import ChatMessage
    session.chat_history.append(ChatMessage(role="user", content=f"[REJECTION] {feedback}"))
    session.state = SessionState.IDLE
    session_store.update(session)
    await emit_state_change(session.id, SessionState.IDLE, "Plan rejected — modify settings and re-run")


async def clarify(session: Session, message: str) -> None:
    """Deliver operator clarification to a paused agent."""
    from models.schemas import ChatMessage
    session.chat_history.append(ChatMessage(role="user", content=message))
    session_store.update(session)

    fut = _clarification_futures.get(session.id)
    if fut and not fut.done():
        fut.set_result(message)

    if session.state == SessionState.WAITING_FOR_INPUT:
        session.state = SessionState.EXECUTING
        session_store.update(session)
        await emit_state_change(session.id, SessionState.EXECUTING, "Resuming after clarification")


async def cancel(session: Session) -> None:
    task = _tasks.pop(session.id, None)
    if task and not task.done():
        task.cancel()
    session.state = SessionState.CANCELLED
    session.completed_at = datetime.now(timezone.utc)
    session_store.update(session)
    await emit_state_change(session.id, SessionState.CANCELLED, "Session cancelled")


# ─── Internal execution flow ─────────────────────────────────────────────

async def _execute(session: Session) -> None:
    """Execute the approved plan in the canonical category order:

    1. Vulnerability scanning  (fast, can fail-fast)
    2. Unit tests              (generated from diff)
    3. Header tests
    4. API + Integration tests (existing api_runner)
    5. Performance tests
    6. UI tests with screencast (slowest)
    7. Script tests
    8. Analyse + report
    """
    try:
        await _run_vuln_scans(session)
        await _run_unit_tests(session)
        await _run_header_tests(session)
        await _run_api_tests(session)
        await _run_perf_tests(session)
        if _should_run_ui_tests(session):
            await _run_ui_tests(session)
        await _run_script_tests(session)
        await _analyse_and_report(session)
    except asyncio.CancelledError:
        log.info("engine.execution_cancelled", context={"session": session.id})
    except Exception as exc:
        log.exception("engine.execution_error", context={"session": session.id})
        session.state = SessionState.FAILED
        session.error_message = str(exc)
        session.completed_at = datetime.now(timezone.utc)
        session_store.update(session)
        await emit_state_change(session.id, SessionState.FAILED, str(exc))


def _plan_has_category(session: Session, category: TestCategory) -> bool:
    """True if any planned test belongs to the given category."""
    if not session.plan:
        return False
    cat_val = category.value
    for item in session.plan.items:
        tc_cat = item.test_case.category
        tc_cat_val = tc_cat.value if hasattr(tc_cat, "value") else str(tc_cat)
        if tc_cat_val == cat_val:
            return True
    return False


async def _emit_result(session: Session, result: TestResult, agent_label: str) -> None:
    """Append, persist, and broadcast a single test result."""
    session.results.append(result)
    session_store.update(session)
    await emit_test_result(session.id, result.model_dump(mode="json"))
    icon = "✓" if result.status == TestStatus.PASSED else ("✗" if result.status == TestStatus.FAILED else "⚠")
    await emit_log(
        session.id,
        f"[{agent_label}] {icon} {result.test_name} — {result.status} ({result.duration_ms}ms)",
        level="INFO" if result.status == TestStatus.PASSED else "WARNING",
    )


async def _run_vuln_scans(session: Session) -> None:
    if not _plan_has_category(session, TestCategory.VULNERABILITY) and session.change_context is None:
        return
    await emit_log(session.id, "[Engine] Running vulnerability scans (pip-audit + bandit + semgrep)…")
    try:
        results = await vuln_scanner.run_all_scans()
    except Exception as exc:
        await emit_log(session.id, f"[VulnScanner] Error: {exc}", level="ERROR")
        return
    for r in results:
        await _emit_result(session, r, "VulnScanner")


async def _run_unit_tests(session: Session) -> None:
    if not _plan_has_category(session, TestCategory.UNIT) and session.change_context is None:
        return
    await emit_log(session.id, "[Engine] Generating + running unit tests…")
    try:
        cs = session.change_context.change_set if session.change_context else None
        results = await unit_runner.run_unit_tests(session.id, cs)
    except Exception as exc:
        await emit_log(session.id, f"[UnitRunner] Error: {exc}", level="ERROR")
        return
    for r in results:
        await _emit_result(session, r, "UnitRunner")


async def _run_header_tests(session: Session) -> None:
    if not _plan_has_category(session, TestCategory.HEADER) and session.change_context is None:
        return
    await emit_log(session.id, "[Engine] Running HTTP security-header tests…")
    try:
        results = await header_runner.run_header_tests()
    except Exception as exc:
        await emit_log(session.id, f"[HeaderRunner] Error: {exc}", level="ERROR")
        return
    for r in results:
        await _emit_result(session, r, "HeaderRunner")


async def _run_perf_tests(session: Session) -> None:
    if not _plan_has_category(session, TestCategory.PERFORMANCE) and session.change_context is None:
        return
    await emit_log(session.id, "[Engine] Running performance probes…")
    try:
        results = await perf_runner.run_perf_tests()
    except Exception as exc:
        await emit_log(session.id, f"[PerfRunner] Error: {exc}", level="ERROR")
        return
    for r in results:
        await _emit_result(session, r, "PerfRunner")


_DEDICATED_RUNNER_CATEGORIES = {
    TestCategory.UNIT.value,
    TestCategory.VULNERABILITY.value,
    TestCategory.HEADER.value,
    TestCategory.PERFORMANCE.value,
    TestCategory.UI.value,
}


def _is_api_runner_case(tc) -> bool:
    """A case belongs to api_runner if it's an HTTP test not handled by a dedicated runner."""
    if tc.script_path:
        return False
    if tc.module == BankingModule.UI:
        return False
    cat_val = tc.category.value if hasattr(tc.category, "value") else str(tc.category)
    if cat_val in _DEDICATED_RUNNER_CATEGORIES:
        return False
    # Must have an endpoint to be runnable as an API test
    return bool(tc.endpoint)


async def _run_api_tests(session: Session) -> None:
    if not session.plan:
        return
    api_cases = [item.test_case for item in session.plan.items if _is_api_runner_case(item.test_case)]
    if not api_cases:
        return

    await emit_log(session.id, f"[Engine] Running {len(api_cases)} API tests…")
    agent = ExecutionAgent(session.id)

    async def _on_result(result: TestResult) -> None:
        await _emit_result(session, result, "ExecutionAgent")

    await agent.run_suite(api_cases, on_result=_on_result)


def _should_run_ui_tests(session: Session) -> bool:
    """Robust check: run UI tests if ANY of these are true.

    1. UI module is selected (string OR enum comparison)
    2. The plan has any TestCase with category == UI
    3. Session is change-driven (always re-verify the customer-facing flow)
    """
    # (1) String-tolerant module check
    ui_value = BankingModule.UI.value
    for m in session.modules:
        mod_str = m.value if hasattr(m, "value") else str(m)
        if mod_str == ui_value:
            return True

    # (2) Any UI category in the plan?
    if session.plan:
        ui_cat_value = TestCategory.UI.value
        for item in session.plan.items:
            cat = item.test_case.category
            cat_val = cat.value if hasattr(cat, "value") else str(cat)
            if cat_val == ui_cat_value:
                return True

    # (3) Change-driven session — always exercise UI end-to-end
    if session.change_context is not None:
        return True

    return False


def _extract_ui_extras_from_plan(session: Session) -> list[dict]:
    """Pull Claude-suggested UI scenarios from the approved plan.

    Returns list of {"name": str, "instruction": str} dicts that UIAgent
    will run AFTER its hardcoded baseline scenarios.
    """
    extras: list[dict] = []
    if not session.plan:
        return extras
    ui_cat_value = TestCategory.UI.value
    for item in session.plan.items:
        tc = item.test_case
        cat = tc.category
        cat_val = cat.value if hasattr(cat, "value") else str(cat)
        if cat_val != ui_cat_value:
            continue
        if tc.script_path:
            continue
        # Prefer the natural-language ui_instruction stashed in payload by the planner
        instruction = ""
        if isinstance(tc.payload, dict):
            instruction = tc.payload.get("ui_instruction", "") or ""
        if not instruction:
            instruction = tc.description or tc.name
        extras.append({"name": tc.name, "instruction": instruction})
    return extras


async def _run_ui_tests(session: Session) -> None:
    """Run UIAgent against the target browser with live CDP screencast.

    Includes:
      1. Hardcoded baseline scenarios (dashboard, search, cards, etc.)
      2. Any Claude-suggested UI scenarios from the change-driven plan

    Failures inside UIAgent (e.g. Chromium crash) are converted to a single
    TestResult.ERROR so the session still completes and reports cleanly.
    """
    extras = _extract_ui_extras_from_plan(session)
    if extras:
        await emit_log(
            session.id,
            f"[Engine] {len(extras)} change-driven UI scenario(s) injected from plan",
        )
    await emit_log(session.id, "[Engine] Starting UI (Computer Use) tests…")
    agent = UIAgent(session.id)
    try:
        ui_results = await agent.run_all_scenarios(extra_scenarios=extras)
        for r in ui_results:
            await _emit_result(session, r, "UIAgent")
    except Exception as exc:
        log.exception("engine.ui_tests_failed", context={"session": session.id})
        # Convert the crash into a visible TestResult so the user sees what happened
        err_result = TestResult(
            test_id=f"ui-runner-error-{session.id[:8]}",
            test_name="UI runner crashed",
            module="UI",
            category="UI",
            status=TestStatus.ERROR,
            duration_ms=0.0,
            error=str(exc)[:500],
        )
        await _emit_result(session, err_result, "UIAgent")
        await emit_log(session.id, f"[UIAgent] Error: {exc}", level="ERROR")


async def _run_script_tests(session: Session) -> None:
    script_cases = [
        item.test_case for item in (session.plan.items if session.plan else [])
        if item.test_case.script_path
    ]
    # Also pick up scripts uploaded directly
    for us in session.uploaded_scripts:
        if us.enabled:
            async def _on_line(line: str) -> None:
                await emit_log(session.id, f"[Script:{us.filename}] {line}")

            result = await script_runner.run_script(us, on_output=_on_line)
            session.results.append(result)
            session_store.update(session)
            await emit_test_result(session.id, result.model_dump(mode="json"))


async def _analyse_and_report(session: Session) -> None:
    await emit_log(session.id, "[Engine] Analysing results and generating report…")

    failed = [r for r in session.results if r.status in (TestStatus.FAILED, TestStatus.ERROR)]
    intel_agent = IntelligenceAgent(session.id)

    rca_data: dict = {}
    if failed:
        try:
            rca_data = await intel_agent.analyse_failures(failed)
            # Attach RCA strings to individual results
            for group in rca_data.get("failure_groups", []):
                rca_text = group.get("rca", "")
                for test_name in group.get("tests", []):
                    for r in session.results:
                        if r.test_name == test_name and not r.rca:
                            r.rca = rca_text
        except Exception as exc:
            await emit_log(session.id, f"[IntelligenceAgent] RCA failed: {exc}", "WARNING")

    executive_summary = rca_data.get("executive_summary", "")
    if not executive_summary:
        try:
            executive_summary = await intel_agent.generate_executive_summary(session.results)
        except Exception:
            executive_summary = ""

    # Build and save report
    report = report_generator.generate(session)
    report.ai_executive_summary = executive_summary
    report_generator.save_json(report)
    report_generator.save_html(report)

    session.report_id = report.id
    session.state = SessionState.COMPLETED
    session.completed_at = datetime.now(timezone.utc)
    session_store.update(session)

    await emit_state_change(session.id, SessionState.COMPLETED, "All tests complete")
    await emit_report_ready(session.id, report.id)
    await emit_log(
        session.id,
        f"[Engine] Done — {report.passed}/{report.total} passed ({round(report.passed/report.total*100 if report.total else 0,1)}%). Report: {report.id}",
    )
