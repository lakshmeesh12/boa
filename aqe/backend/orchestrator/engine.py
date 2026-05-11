"""Orchestration Engine — owns the session state machine and coordinates all agents."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from core.event_bus import (
    emit_log, emit_report_ready, emit_state_change, emit_test_result,
)
from core.logging_config import get_logger
from models.schemas import (
    Plan, Session, SessionState, TestResult, TestStatus, TestType,
    BankingModule,
)
from orchestrator import session_store
from orchestrator.agents.execution_agent import ExecutionAgent
from orchestrator.agents.intelligence_agent import IntelligenceAgent
from orchestrator.agents.ui_agent import UIAgent
from orchestrator.planner import generate_plan
from test_runner import report_generator, script_runner

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
    try:
        await _run_api_tests(session)
        if BankingModule.UI in session.modules:
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


async def _run_api_tests(session: Session) -> None:
    if not session.plan:
        return
    api_cases = [
        item.test_case for item in session.plan.items
        if not item.test_case.script_path and item.test_case.module != BankingModule.UI
    ]
    if not api_cases:
        return

    await emit_log(session.id, f"[Engine] Running {len(api_cases)} API tests…")
    agent = ExecutionAgent(session.id)

    async def _on_result(result: TestResult) -> None:
        session.results.append(result)
        session_store.update(session)
        await emit_test_result(session.id, result.model_dump(mode="json"))
        icon = "✓" if result.status == TestStatus.PASSED else "✗"
        await emit_log(
            session.id,
            f"[ExecutionAgent] {icon} {result.test_name} — {result.status} ({result.duration_ms}ms)",
            level="INFO" if result.status == TestStatus.PASSED else "WARNING",
        )

    await agent.run_suite(api_cases, on_result=_on_result)


async def _run_ui_tests(session: Session) -> None:
    await emit_log(session.id, "[Engine] Starting UI (Computer Use) tests…")
    agent = UIAgent(session.id)
    try:
        ui_results = await agent.run_all_scenarios()
        for r in ui_results:
            session.results.append(r)
        session_store.update(session)
    except Exception as exc:
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
