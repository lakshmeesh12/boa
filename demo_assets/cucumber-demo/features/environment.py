"""Behave hooks for the AQE Cucumber demo.

Surfaces the target URL / token early so failures during Background steps
emit a clear message instead of a generic connection error deep in a step.
AQE injects the env vars before spawning behave (see
aqe/backend/test_runner/script_runner.py::_build_env).
"""
from __future__ import annotations

import os


def before_all(context):
    api = (os.environ.get("TARGET_API_URL") or "").strip()
    context.config.userdata["target_api_url"] = api
    # Stash on the context so step defs can read it consistently
    context.target_api_url = api
    print(f"[behave] before_all: TARGET_API_URL={api or '(not set — defaulting to localhost:8000)'}")


def before_scenario(context, scenario):
    # Reset per-scenario state so cross-scenario contamination is impossible.
    context.last_response = None
    context.responses = []
    context.card_id = None
