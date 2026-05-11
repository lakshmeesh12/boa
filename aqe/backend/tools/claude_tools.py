"""Claude tool schemas and executor dispatch used by all AQE agents."""
from __future__ import annotations

import json
from typing import Any

import httpx

from core.logging_config import get_logger
from core.settings import settings

log = get_logger("ClaudeTools")

# ─── Tool schemas (passed to Claude in `tools` list) ─────────────────────

RUN_API_TEST = {
    "name": "run_api_test",
    "description": "Execute an HTTP request against the Target Banking API and return the response.",
    "input_schema": {
        "type": "object",
        "properties": {
            "method":   {"type": "string", "enum": ["GET","POST","PUT","DELETE"]},
            "endpoint": {"type": "string", "description": "Path relative to target API, e.g. /api/v1/customers"},
            "payload":  {"type": "object", "description": "JSON request body (POST/PUT)"},
            "expected_status": {"type": "integer", "description": "Expected HTTP status code"},
        },
        "required": ["method", "endpoint"],
    },
}

QUERY_QDRANT = {
    "name": "query_qdrant",
    "description": "Semantic search over ingested banking API logs stored in Qdrant. Use this to find similar past failures or patterns.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural-language search query"},
            "limit": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    },
}

QUERY_NEO4J = {
    "name": "query_neo4j",
    "description": "Run a Cypher query against the Neo4j graph to find test/module/error relationships.",
    "input_schema": {
        "type": "object",
        "properties": {
            "cypher": {"type": "string", "description": "Cypher query to execute"},
        },
        "required": ["cypher"],
    },
}

TAKE_SCREENSHOT = {
    "name": "take_screenshot",
    "description": "Capture the current state of the banking UI browser (returns base64 PNG).",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

NAVIGATE = {
    "name": "navigate",
    "description": "Navigate the browser to a URL.",
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full URL to navigate to"},
        },
        "required": ["url"],
    },
}

EXECUTE_BROWSER_ACTION = {
    "name": "execute_browser_action",
    "description": "Execute a browser action (click, type, scroll, key press) at specified coordinates.",
    "input_schema": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["left_click","type","key","scroll","double_click","mouse_move","screenshot"]},
            "coordinate": {"type": "array", "items": {"type": "integer"}, "description": "[x, y] pixel coordinates"},
            "text": {"type": "string", "description": "Text to type, or key name (e.g. Enter, Tab)"},
            "scroll_direction": {"type": "string", "enum": ["up","down"]},
            "scroll_distance": {"type": "integer"},
        },
        "required": ["type"],
    },
}

EMIT_CLARIFICATION = {
    "name": "emit_clarification_request",
    "description": "Pause execution and ask the human operator a question before proceeding.",
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The question to surface to the operator"},
        },
        "required": ["question"],
    },
}

RECORD_TEST_RESULT = {
    "name": "record_test_result",
    "description": "Record the outcome of a UI test scenario.",
    "input_schema": {
        "type": "object",
        "properties": {
            "test_name": {"type": "string"},
            "status": {"type": "string", "enum": ["PASSED","FAILED","ERROR"]},
            "observation": {"type": "string", "description": "What was observed on screen"},
            "error": {"type": "string"},
        },
        "required": ["test_name", "status", "observation"],
    },
}

# Tool sets per agent role
EXECUTION_TOOLS    = [RUN_API_TEST, EMIT_CLARIFICATION]
UI_TOOLS           = [TAKE_SCREENSHOT, NAVIGATE, EXECUTE_BROWSER_ACTION, RECORD_TEST_RESULT, EMIT_CLARIFICATION]
INTELLIGENCE_TOOLS = [QUERY_QDRANT, QUERY_NEO4J, RUN_API_TEST]
DISCOVERY_TOOLS    = [RUN_API_TEST]


# ─── Executor ────────────────────────────────────────────────────────────
class ToolExecutor:
    """Dispatches tool_use blocks returned by Claude to the actual implementation."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._qdrant = None
        self._neo4j  = None

    async def execute(self, tool_name: str, tool_input: dict) -> Any:
        log.info("tool.execute", context={"tool": tool_name, "session": self.session_id})
        try:
            if tool_name == "run_api_test":
                return await self._run_api_test(**tool_input)
            elif tool_name == "query_qdrant":
                return await self._query_qdrant(**tool_input)
            elif tool_name == "query_neo4j":
                return await self._query_neo4j(**tool_input)
            elif tool_name == "take_screenshot":
                from test_runner import playwright_runner as pr
                b64 = await pr.take_screenshot()
                return {"screenshot_b64": b64[:100] + "...(truncated for tool result)"}
            elif tool_name == "navigate":
                from test_runner import playwright_runner as pr
                await pr.navigate(tool_input["url"])
                return {"navigated_to": tool_input["url"]}
            elif tool_name == "execute_browser_action":
                from test_runner import playwright_runner as pr
                b64 = await pr.execute_action(tool_input)
                return {"screenshot_b64": b64[:100] + "...", "action_executed": tool_input.get("type")}
            elif tool_name == "record_test_result":
                return tool_input  # bubbled up to the agent
            elif tool_name == "emit_clarification_request":
                from core import event_bus
                await event_bus.emit_clarification_request(self.session_id, tool_input["question"])
                return {"status": "clarification_requested", "question": tool_input["question"]}
            else:
                return {"error": f"unknown tool: {tool_name}"}
        except Exception as exc:
            log.exception("tool.error", context={"tool": tool_name})
            return {"error": str(exc)}

    async def _run_api_test(
        self,
        method: str,
        endpoint: str,
        payload: dict | None = None,
        expected_status: int = 200,
    ) -> dict:
        url = settings.target_api_url.rstrip("/") + endpoint
        async with httpx.AsyncClient(timeout=15) as client:
            kwargs: dict = {}
            if payload:
                kwargs["json"] = payload
            resp = await getattr(client, method.lower())(url, **kwargs)
            body: dict = {}
            try:
                body = resp.json()
            except Exception:
                body = {"raw": resp.text[:300]}
            passed = resp.status_code == expected_status
            return {
                "status_code": resp.status_code,
                "expected_status": expected_status,
                "passed": passed,
                "body": body,
                "trace_id": resp.headers.get("x-trace-id"),
            }

    async def _query_qdrant(self, query: str, limit: int = 5) -> dict:
        try:
            from graphrag.qdrant_engine import search_logs
            results = await search_logs(query, limit=limit)
            return {"results": results}
        except Exception as exc:
            return {"error": str(exc), "results": []}

    async def _query_neo4j(self, cypher: str) -> dict:
        try:
            from graphrag.neo4j_engine import run_cypher
            results = await run_cypher(cypher)
            return {"results": results}
        except Exception as exc:
            return {"error": str(exc), "results": []}
