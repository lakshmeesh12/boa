"""Discovery Agent — introspects target API and analyses uploaded scripts to build a test plan."""
from __future__ import annotations

from models.schemas import BankingModule, TestType, UploadedScript
from tools.claude_tools import DISCOVERY_TOOLS
from .base_agent import BaseAgent

_SYSTEM = """You are an expert Quality Engineering AI. Your task is to analyse a banking API
and produce a structured test plan in JSON.

When called with the target API URL, introspect its health endpoint and a few sample responses,
then return a JSON object with:
{
  "ai_summary": "...",
  "test_cases": [
    {
      "name": "...",
      "description": "...",
      "module": "CreditCards | Deposits | Transactions | Customers | Accounts",
      "test_type": "Functional | EdgeCase | Security",
      "method": "GET | POST",
      "endpoint": "/api/v1/...",
      "payload": {...} or null,
      "expected_status": 200,
      "rationale": "..."
    }
  ]
}

Focus on:
- Happy-path functional tests
- Edge cases (invalid IDs, constraint violations, frozen/blocked entities)
- Security checks (PII not leaked, hash fields not in response)
Keep the list focused and practical. Return ONLY valid JSON, no prose before/after.
"""


class DiscoveryAgent(BaseAgent):
    name = "DiscoveryAgent"
    system_prompt = _SYSTEM
    tool_schemas = DISCOVERY_TOOLS
    max_iterations = 8

    async def discover(
        self,
        modules: list[BankingModule],
        test_types: list[TestType],
        uploaded_scripts: list[UploadedScript],
    ) -> dict:
        """Run discovery and return parsed plan dict."""
        script_info = ""
        if uploaded_scripts:
            script_info = "\n\nUser-uploaded scripts to summarise:\n" + "\n".join(
                f"- {s.filename} ({s.script_type})" for s in uploaded_scripts
            )

        module_names = ", ".join(str(m) for m in modules)
        type_names   = ", ".join(str(t) for t in test_types)

        prompt = (
            f"Target API: {__import__('core.settings', fromlist=['settings']).settings.target_api_url}\n"
            f"Modules to test: {module_names}\n"
            f"Test types: {type_names}\n"
            f"Use the run_api_test tool to probe /health and /api/v1/customers?limit=1 to understand live data."
            + script_info
            + "\n\nNow return the full test plan JSON."
        )

        raw = await self.run(prompt)

        # Extract JSON from the response
        import json, re
        match = re.search(r'\{[\s\S]+\}', raw)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {"ai_summary": raw[:500], "test_cases": []}
