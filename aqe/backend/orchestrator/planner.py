"""Planner — generates a test plan via the Discovery Agent and waits for human approval.

Two modes:
  * Standard: built-in suite + Discovery Agent's AI suggestions  (no change context)
  * Change-driven: filters/extends with ChangeAnalysis suggestions, respecting plan_mode:
      - smart    : impacted existing tests + new tests from the diff
      - full     : entire suite + new tests from the diff
      - new_only : ONLY the new tests Claude generated for the diff
"""
from __future__ import annotations

import uuid

from core.event_bus import emit_log, emit_plan, emit_state_change
from core.logging_config import get_logger
from models.schemas import (
    BankingModule, ChangeContext, Plan, PlanItem, PlanMode, Session, SessionState,
    SuggestedTest, TestCase, TestCategory, TestType, UploadedScript,
)
from orchestrator.agents.discovery_agent import DiscoveryAgent
from test_suites.base import get_all_cases

log = get_logger("Planner")


# ─── helpers ────────────────────────────────────────────────────────────


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


def _category_to_test_type(cat: TestCategory) -> TestType:
    """Map the broader TestCategory back to legacy TestType for compatibility."""
    if cat == TestCategory.SECURITY:
        return TestType.SECURITY
    if cat in (TestCategory.HEADER, TestCategory.VULNERABILITY, TestCategory.PERFORMANCE, TestCategory.UNIT):
        return TestType.FUNCTIONAL  # broad bucket; the runner picks via category
    if cat == TestCategory.UI:
        return TestType.FUNCTIONAL
    return TestType.FUNCTIONAL


def _suggested_to_test_case(s: SuggestedTest, triggered_by: list[str]) -> TestCase:
    """Convert a Claude-suggested test (from ChangeAnalysis) into an executable TestCase."""
    return TestCase(
        name=s.name,
        description=s.description or s.rationale,
        module=s.module,
        test_type=_category_to_test_type(s.category),
        category=s.category,
        method=s.method,
        endpoint=s.endpoint,
        payload=s.payload,
        expected_status=s.expected_status,
        triggered_by_files=triggered_by,
    )


def _filter_impacted_builtins(
    builtin_cases: list[TestCase],
    affected_modules: list[str],
    session_modules: list[BankingModule] | None = None,
) -> list[TestCase]:
    """Smart mode: keep tests whose module is impacted by the diff.

    A test is impacted if EITHER:
      (a) its module appears in Claude's modules_affected list, OR
      (b) it's a UI test and UI is among the session's selected modules
          (UI flows always need to be re-verified when ANY backend changes —
           every banking endpoint can be reached from the customer portal).
    """
    affected_set = {m.lower() for m in (affected_modules or [])}
    session_mods = session_modules or []
    session_mod_values = {
        (m.value if hasattr(m, "value") else str(m)).lower() for m in session_mods
    }
    ui_in_session = BankingModule.UI.value.lower() in session_mod_values

    impacted: list[TestCase] = []
    for tc in builtin_cases:
        mod_str = (tc.module.value if hasattr(tc.module, "value") else str(tc.module)).lower()
        if mod_str in affected_set:
            impacted.append(tc)
        elif ui_in_session and mod_str == BankingModule.UI.value.lower():
            impacted.append(tc)  # UI tests always run if UI is selected
    return impacted


def _script_cases(scripts: list[UploadedScript]) -> list[TestCase]:
    out: list[TestCase] = []
    for script in scripts:
        if not script.enabled:
            continue
        from models.schemas import ScriptType  # local import to avoid cycle
        out.append(TestCase(
            name=f"[Script] {script.filename}",
            description=script.ai_summary or f"User-uploaded {script.script_type} script",
            module=BankingModule.CUSTOM_SCRIPT,
            test_type=TestType.FUNCTIONAL,
            category=TestCategory.API,
            script_path=script.file_path,
            script_type=script.script_type,
        ))
    return out


# ─── plan builders ──────────────────────────────────────────────────────


async def _generate_standard_plan(session: Session) -> Plan:
    """Original flow — no change context. Built-in suites + Discovery Agent."""
    builtin_cases = get_all_cases(
        session.modules,
        session.test_types[0] if session.test_types else TestType.ALL,
    )

    agent = DiscoveryAgent(session.id)
    try:
        ai_output = await agent.discover(session.modules, session.test_types, session.uploaded_scripts)
    except Exception as exc:
        log.warning("planner.discovery_failed", context={"error": str(exc)})
        ai_output = {"ai_summary": "Discovery agent unavailable — using built-in suites only.", "test_cases": []}

    ai_cases = _ai_cases_to_test_cases(ai_output.get("test_cases", []), session.modules)
    ai_summary = ai_output.get("ai_summary", "")
    script_cases = _script_cases(session.uploaded_scripts)
    all_cases = builtin_cases + ai_cases + script_cases

    return Plan(
        id=str(uuid.uuid4()),
        session_id=session.id,
        ai_summary=ai_summary,
        total_cases=len(all_cases),
        items=[
            PlanItem(
                test_case=tc, order=i,
                rationale="Built-in suite" if tc.script_path is None else "User script",
            )
            for i, tc in enumerate(all_cases)
        ],
    )


async def _generate_change_driven_plan(session: Session) -> Plan:
    """Change-driven flow — uses session.change_context.analysis + plan_mode."""
    ctx: ChangeContext = session.change_context  # type: ignore[assignment]
    analysis = ctx.analysis
    triggered_files = [f.path for f in ctx.change_set.files]

    # Convert Claude's suggested_new_tests → TestCase
    new_cases: list[TestCase] = [_suggested_to_test_case(s, triggered_files) for s in analysis.suggested_new_tests]

    plan_mode = session.plan_mode if isinstance(session.plan_mode, PlanMode) else PlanMode(session.plan_mode)
    rationale_by_case: dict[str, str] = {}

    if plan_mode == PlanMode.NEW_ONLY:
        all_cases = new_cases
        for s, tc in zip(analysis.suggested_new_tests, new_cases):
            rationale_by_case[tc.id] = f"NEW-ONLY: {s.rationale}"

    elif plan_mode == PlanMode.SMART:
        builtin_all = get_all_cases(session.modules or [], TestType.ALL)
        impacted = _filter_impacted_builtins(
            builtin_all, analysis.modules_affected, session.modules or [],
        )
        for tc in impacted:
            mod_lower = (tc.module.value if hasattr(tc.module, "value") else str(tc.module)).lower()
            if mod_lower == BankingModule.UI.value.lower():
                rationale_by_case[tc.id] = "SMART: UI regression (always run on any change)"
            else:
                rationale_by_case[tc.id] = "SMART: covers module impacted by diff"
        for s, tc in zip(analysis.suggested_new_tests, new_cases):
            rationale_by_case[tc.id] = f"SMART (new): {s.rationale}"
        all_cases = impacted + new_cases

    else:  # FULL
        all_modules = session.modules or list(BankingModule)
        builtin_all = get_all_cases(all_modules, TestType.ALL)
        for tc in builtin_all:
            rationale_by_case[tc.id] = "FULL: regression coverage"
        for s, tc in zip(analysis.suggested_new_tests, new_cases):
            rationale_by_case[tc.id] = f"FULL (new): {s.rationale}"
        all_cases = builtin_all + new_cases

    # Always include uploaded scripts
    script_cases = _script_cases(session.uploaded_scripts)
    for tc in script_cases:
        rationale_by_case[tc.id] = "User script"
    all_cases = all_cases + script_cases

    summary_lines = [
        analysis.summary or "Change-driven plan.",
        f"Mode: {plan_mode.value if isinstance(plan_mode, PlanMode) else plan_mode}.",
        f"{ctx.change_set.file_count} file(s) changed since baseline.",
        f"Risk: {analysis.risk_level}.",
    ]
    if analysis.detected_issues:
        summary_lines.append("Detected issues: " + "; ".join(analysis.detected_issues[:3]))

    return Plan(
        id=str(uuid.uuid4()),
        session_id=session.id,
        ai_summary=" ".join(summary_lines),
        total_cases=len(all_cases),
        items=[
            PlanItem(
                test_case=tc, order=i,
                rationale=rationale_by_case.get(tc.id, "Built-in suite"),
            )
            for i, tc in enumerate(all_cases)
        ],
    )


async def generate_plan(session: Session) -> Plan:
    """Top-level planner — routes to change-driven or standard flow."""
    await emit_state_change(session.id, SessionState.PLANNING, "Generating test plan…")

    if session.change_context is not None:
        await emit_log(
            session.id,
            f"[Planner] Change-driven plan ({session.plan_mode}) — "
            f"{session.change_context.change_set.file_count} files, "
            f"risk={session.change_context.analysis.risk_level}",
        )
        plan = await _generate_change_driven_plan(session)
    else:
        plan = await _generate_standard_plan(session)

    log.info("planner.plan_generated", context={"session": session.id, "total": plan.total_cases})
    await emit_plan(session.id, {
        "plan_id": plan.id,
        "ai_summary": plan.ai_summary,
        "total_cases": plan.total_cases,
        "items": [
            {
                "name": item.test_case.name,
                "module": str(item.test_case.module),
                "test_type": str(item.test_case.test_type),
                "category": str(item.test_case.category),
                "rationale": item.rationale,
            }
            for item in plan.items
        ],
    })
    await emit_state_change(session.id, SessionState.AWAITING_APPROVAL, "Plan ready for review")
    return plan
