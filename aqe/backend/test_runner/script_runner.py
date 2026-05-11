"""Subprocess-based runner for user-uploaded test scripts (Bash, Python, Selenium)."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Callable, Awaitable

from core.logging_config import get_logger
from core.settings import settings
from models.schemas import ScriptType, TestResult, TestStatus, UploadedScript

log = get_logger("ScriptRunner")

DEFAULT_TIMEOUT = 120  # seconds


def _detect_script_type(filename: str) -> ScriptType:
    ext = Path(filename).suffix.lower()
    if ext in (".sh", ".bash"):
        return ScriptType.BASH
    if ext in (".feature",):
        return ScriptType.FEATURE
    # Python: check if it likely uses selenium
    return ScriptType.PYTHON


def _build_env(script_type: ScriptType) -> dict:
    """Inject target URLs and useful env vars into the subprocess environment."""
    env = os.environ.copy()
    env["TARGET_API_URL"]  = settings.target_api_url
    env["TARGET_UI_URL"]   = settings.target_ui_url
    env["SELENIUM_TARGET"] = settings.target_ui_url  # For selenium scripts
    env["AQE_RUN"]         = "1"
    return env


async def run_script(
    script: UploadedScript,
    on_output: Callable[[str], Awaitable[None]] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> TestResult:
    """Run a user-uploaded script as a subprocess, streaming output to on_output."""
    path = Path(script.file_path)
    if not path.exists():
        return TestResult(
            test_id=script.id,
            test_name=script.filename,
            module="CustomScript",
            status=TestStatus.ERROR,
            duration_ms=0,
            error=f"Script file not found: {path}",
        )

    stype = script.script_type
    if stype == ScriptType.BASH:
        cmd = ["bash", str(path)]
    elif stype == ScriptType.FEATURE:
        # Try behave (Cucumber-style) first, fall back to echo
        cmd = ["python", "-m", "behave", str(path)]
    else:
        cmd = [sys.executable, str(path)]

    env = _build_env(stype)
    start = time.perf_counter()
    output_lines: list[str] = []

    log.info("script_runner.start", context={"script": script.filename, "cmd": cmd[0]})

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # merge stderr into stdout
            env=env,
            cwd=str(path.parent),
        )

        async def _read_output():
            assert proc.stdout
            async for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                output_lines.append(line)
                if on_output:
                    await on_output(line)

        try:
            await asyncio.wait_for(
                asyncio.gather(_read_output(), proc.wait()),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            duration_ms = (time.perf_counter() - start) * 1000
            return TestResult(
                test_id=script.id,
                test_name=script.filename,
                module="CustomScript",
                status=TestStatus.ERROR,
                duration_ms=round(duration_ms, 1),
                response_summary="\n".join(output_lines[-20:]),
                error=f"Script timed out after {timeout}s",
            )

        duration_ms = (time.perf_counter() - start) * 1000
        exit_code = proc.returncode or 0
        status = TestStatus.PASSED if exit_code == 0 else TestStatus.FAILED

        log.info(
            "script_runner.complete",
            context={"script": script.filename, "exit_code": exit_code, "duration_ms": round(duration_ms, 1)},
        )
        return TestResult(
            test_id=script.id,
            test_name=script.filename,
            module="CustomScript",
            status=status,
            duration_ms=round(duration_ms, 1),
            request_summary=f"exec: {' '.join(cmd)}",
            response_summary="\n".join(output_lines[-50:]),
            error=None if status == TestStatus.PASSED else f"exit code {exit_code}",
        )

    except FileNotFoundError as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        return TestResult(
            test_id=script.id,
            test_name=script.filename,
            module="CustomScript",
            status=TestStatus.ERROR,
            duration_ms=round(duration_ms, 1),
            error=f"Interpreter not found: {exc}",
        )
    except Exception as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        return TestResult(
            test_id=script.id,
            test_name=script.filename,
            module="CustomScript",
            status=TestStatus.ERROR,
            duration_ms=round(duration_ms, 1),
            error=str(exc),
        )
