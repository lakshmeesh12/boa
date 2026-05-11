"""Unit-test runner.

For change-driven sessions, Claude generates pytest files targeting the
functions touched by the diff. The runner writes them under
`aqe/data/generated_tests/{session_id}/`, validates with `py_compile`, then
runs `pytest --json-report` and converts each test outcome into a TestResult.

If no diff is present (non-change-driven session), this runner emits a single
SKIPPED TestResult and returns.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

import anthropic

from core.logging_config import get_logger
from core.settings import settings
from models.schemas import ChangeSet, TestResult, TestStatus

log = get_logger("UnitRunner")
_client = anthropic.Anthropic(api_key=settings.claude_api_key)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GENERATED_ROOT = settings.data_dir / "generated_tests"
_GENERATED_ROOT.mkdir(parents=True, exist_ok=True)

_PY_FILE_PATTERN = re.compile(r"^backend/.+\.py$")
_MAX_FILES_TO_TEST = 6      # cap so Claude doesn't burn tokens on the whole repo

_GENERATOR_SYSTEM_PROMPT = """You are AQE's unit-test generator.

Input: a single Python file's unified diff (from a FastAPI banking app).

Output: a self-contained pytest file that tests the PURE/UNIT logic added or modified in the diff.

Rules:
- Output ONLY Python code — no markdown fences, no preamble, no explanation.
- Do NOT import the target file via package paths; use a sys.path insertion at the top so the file imports cleanly from the repo root.
- For each new/modified function that has testable pure logic, write 2-4 tests covering happy path + at least one edge case.
- If the diff has nothing testable as a unit (only routing / I/O), output exactly:    # NO_UNIT_TESTS_NEEDED
  on its own line and nothing else.
- Use only the stdlib + pytest. Do NOT import httpx, requests, or motor.
- Keep the file under 200 lines.
- Use descriptive test names: test_<function>_<scenario>.
- Be defensive about imports — if a needed symbol may not exist, skip with pytest.skip()."""


def _sanitize_filename(s: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", s.replace(".py", ""))
    return safe.strip("_")[:80]


async def _generate_pytest_for_file(file_path: str, diff_text: str) -> str | None:
    """Ask Claude for pytest code for a single changed file's diff. Returns code or None."""
    user_prompt = f"File: {file_path}\n\nDiff:\n{diff_text}\n"
    response = _client.messages.create(
        model=settings.claude_model_sonnet,
        max_tokens=4096,
        system=_GENERATOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = next((b.text for b in response.content if hasattr(b, "text")), "").strip()
    # Strip code fences if Claude included them despite instructions.
    m = re.match(r"^```(?:python)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    if m:
        text = m.group(1).strip()
    if not text or "NO_UNIT_TESTS_NEEDED" in text:
        return None
    return text


def _wrap_with_path_setup(code: str) -> str:
    """Prepend a sys.path insertion so generated tests can import from the repo backend."""
    preamble = (
        "import sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, r'{_REPO_ROOT}')\n"
        f"sys.path.insert(0, r'{_REPO_ROOT / 'backend'}')\n"
    )
    if preamble.strip().splitlines()[0] in code:
        return code
    return preamble + "\n" + code


def _validate_compiles(path: Path) -> tuple[bool, str]:
    import py_compile
    try:
        py_compile.compile(str(path), doraise=True)
        return True, ""
    except py_compile.PyCompileError as exc:
        return False, str(exc)


async def _run_pytest(test_dir: Path) -> tuple[int, dict]:
    """Run pytest --json-report on test_dir. Returns (returncode, parsed_report_json)."""
    if not shutil.which("pytest"):
        return -1, {"error": "pytest not installed. pip install pytest pytest-json-report"}
    report_path = test_dir / "pytest_report.json"
    env = os.environ.copy()
    proc = await asyncio.create_subprocess_exec(
        "pytest",
        str(test_dir),
        "--json-report",
        f"--json-report-file={report_path}",
        "-q", "--no-header",
        cwd=str(_REPO_ROOT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    if report_path.exists():
        try:
            return proc.returncode or 0, json.loads(report_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return proc.returncode or 0, {"error": f"failed to parse report: {exc}"}
    return proc.returncode or 0, {"error": "no pytest-json-report output; install pytest-json-report"}


def _make_skip(reason: str) -> TestResult:
    return TestResult(
        test_id=f"unit-skip-{uuid.uuid4().hex[:8]}",
        test_name="Unit tests skipped",
        module="Infrastructure",
        category="Unit",
        status=TestStatus.SKIPPED,
        duration_ms=0.0,
        error=reason,
    )


async def run_unit_tests(session_id: str, change_set: ChangeSet | None) -> list[TestResult]:
    if change_set is None or change_set.is_empty:
        return [_make_skip("No diff available — unit-test generation skipped.")]

    py_files = [f for f in change_set.files if _PY_FILE_PATTERN.match(f.path) and f.status != "deleted"]
    if not py_files:
        return [_make_skip("Diff contains no backend Python files — nothing to unit-test.")]

    session_dir = _GENERATED_ROOT / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    results: list[TestResult] = []
    generated_count = 0

    for f in py_files[:_MAX_FILES_TO_TEST]:
        t0 = time.perf_counter()
        try:
            code = await _generate_pytest_for_file(f.path, f.diff)
        except Exception as exc:
            results.append(TestResult(
                test_id=f"unit-gen-{_sanitize_filename(f.path)}",
                test_name=f"Generate unit tests for {f.path}",
                module="Infrastructure",
                category="Unit",
                status=TestStatus.ERROR,
                duration_ms=round((time.perf_counter() - t0) * 1000, 1),
                error=f"generator failed: {exc}",
            ))
            continue

        if not code:
            results.append(TestResult(
                test_id=f"unit-noop-{_sanitize_filename(f.path)}",
                test_name=f"Unit tests for {f.path}",
                module="Infrastructure",
                category="Unit",
                status=TestStatus.SKIPPED,
                duration_ms=round((time.perf_counter() - t0) * 1000, 1),
                response_summary="No unit-testable logic in diff.",
            ))
            continue

        code = _wrap_with_path_setup(code)
        out_path = session_dir / f"test_{_sanitize_filename(f.path)}.py"
        out_path.write_text(code, encoding="utf-8")

        ok, compile_err = _validate_compiles(out_path)
        if not ok:
            results.append(TestResult(
                test_id=f"unit-compile-{_sanitize_filename(f.path)}",
                test_name=f"Compile generated tests for {f.path}",
                module="Infrastructure",
                category="Unit",
                status=TestStatus.ERROR,
                duration_ms=round((time.perf_counter() - t0) * 1000, 1),
                error=f"py_compile failed: {compile_err[:300]}",
            ))
            continue

        generated_count += 1

    if generated_count == 0:
        return results  # only skip/error results — nothing to run

    rc, report = await _run_pytest(session_dir)
    if "error" in report:
        results.append(TestResult(
            test_id=f"unit-run-error-{uuid.uuid4().hex[:6]}",
            test_name="pytest run",
            module="Infrastructure",
            category="Unit",
            status=TestStatus.ERROR,
            duration_ms=0.0,
            error=report["error"],
        ))
        return results

    for t in report.get("tests", []):
        outcome = t.get("outcome", "error")
        nodeid = t.get("nodeid", "?")
        duration_ms = round((t.get("duration", 0.0) or 0.0) * 1000, 1)
        if outcome == "passed":
            status = TestStatus.PASSED
            err = None
        elif outcome == "failed":
            status = TestStatus.FAILED
            err = t.get("call", {}).get("longrepr", "")[:400] if isinstance(t.get("call"), dict) else ""
        elif outcome == "skipped":
            status = TestStatus.SKIPPED
            err = None
        else:
            status = TestStatus.ERROR
            err = t.get("longrepr", "")[:400] if isinstance(t.get("longrepr"), str) else ""

        results.append(TestResult(
            test_id=f"unit-{_sanitize_filename(nodeid)}",
            test_name=nodeid.split("::")[-1] if "::" in nodeid else nodeid,
            module="Infrastructure",
            category="Unit",
            status=status,
            duration_ms=duration_ms,
            response_summary=nodeid,
            error=err if err else None,
        ))

    log.info("unit_runner.done", context={"results": len(results), "generated_files": generated_count})
    return results
