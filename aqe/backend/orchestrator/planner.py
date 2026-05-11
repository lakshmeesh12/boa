"""Planner — generates a test plan via the Discovery Agent and waits for human approval."""
from __future__ import annotations

import uuid

from core.event_bus import emit_plan, emit_state_change
from core.logging_config import get_logger
from models.schemas import (
    BankingModule, Plan, PlanItem, Session, SessionState, TestCase,
    TestType, UploadedScript,
)
from orchestrator.agents.discovery_agent import DiscoveryAgent
from test_suites.base import get_all_cases

log = get_logger("Planner")


def _ai_cases_to_test_cases(ai_cases: list[dict], modules: list[BankingModule]) -> list[TestCase]:
    """Convert Discovery Agent JSON output to TestCase objects."""
    cases = []
    for i, item in enumerate(ai_cases):
        try:
            cases.append(TestCase(
                name=item.get("name", f"AI Test {i+1}"),
                description=item.get("description", ""),
                module=BankingModule(item.get("module", "Customers")),
                test_type=TestType(item.get("test_type", "Functional")),
                method=item.get("method", "GET"),
                endpoint=item.get("endpoint", ""),
                payload=item.get("payload"),
                expected_status=int(item.get("expected_status", 200)),
            ))
        except Exception:
            pass
    return cases


async def generate_plan(session: Session) -> Plan:
    """Run Discovery Agent + merge with built-in suites; emit plan for human review."""
    await emit_state_change(session.id, SessionState.PLANNING, "Generating test plan…")

    # 1. Built-in declarative tests
    builtin_cases = get_all_cases(session.modules, session.test_types[0] if session.test_types else TestType.ALL)

    # 2. AI-generated additional tests via Discovery Agent
    agent = DiscoveryAgent(session.id)
    try:
        ai_output = await agent.discover(session.modules, session.test_types, session.uploaded_scripts)
    except Exception as exc:
        log.warning("planner.discovery_failed", context={"error": str(exc)})
        ai_output = {"ai_summary": "Discovery agent unavailable — using built-in suites only.", "test_cases": []}

    ai_cases = _ai_cases_to_test_cases(ai_output.get("test_cases", []), session.modules)
    ai_summary = ai_output.get("ai_summary", "")

    # 3. Script-based test cases
    script_cases: list[TestCase] = []
    for script in session.uploaded_scripts:
        if script.enabled:
            from models.schemas import ScriptType
            script_cases.append(TestCase(
                name=f"[Script] {script.filename}",
                description=script.ai_summary or f"User-uploaded {script.script_type} script",
                module=BankingModule.CUSTOM_SCRIPT,
                test_type=TestType.FUNCTIONAL,
                script_path=script.file_path,
                script_type=script.script_type,
            ))

    all_cases = builtin_cases + ai_cases + script_cases

    plan = Plan(
        id=str(uuid.uuid4()),
        session_id=session.id,
        ai_summary=ai_summary,
        total_cases=len(all_cases),
        items=[
            PlanItem(test_case=tc, order=i, rationale="Built-in suite" if tc.script_path is None else "User script")
            for i, tc in enumerate(all_cases)
        ],
    )

    log.info("planner.plan_generated", context={"session": session.id, "total": len(all_cases)})
    await emit_plan(session.id, {
        "plan_id": plan.id,
        "ai_summary": plan.ai_summary,
        "total_cases": plan.total_cases,
        "items": [
            {"name": item.test_case.name, "module": str(item.test_case.module),
             "test_type": str(item.test_case.test_type), "rationale": item.rationale}
            for item in plan.items
        ],
    })
    await emit_state_change(session.id, SessionState.AWAITING_APPROVAL, "Plan ready for review")
    return plan
