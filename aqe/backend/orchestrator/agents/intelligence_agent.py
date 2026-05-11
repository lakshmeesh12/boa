"""Intelligence Agent — queries GraphRAG to produce root-cause analysis for failures."""
from __future__ import annotations

from models.schemas import TestResult, TestStatus
from tools.claude_tools import INTELLIGENCE_TOOLS
from .base_agent import BaseAgent

_SYSTEM = """You are an expert banking systems QA analyst with access to:
1. Qdrant — a vector database of structured API logs (semantic search)
2. Neo4j — a graph database mapping test cases, banking modules, and errors

When given a list of failed test cases, you must:
- Search Qdrant for similar historical log patterns
- Query Neo4j for blast-radius (which modules are affected downstream)
- Produce a concise Root Cause Analysis (RCA) for each failure
- Identify if failures are related (shared root cause)
- Suggest remediation steps

Return a structured JSON object:
{
  "executive_summary": "...",
  "failure_groups": [
    {
      "common_cause": "...",
      "tests": ["test name 1", "test name 2"],
      "blast_radius": ["Module A", "Module B"],
      "rca": "...",
      "remediation": "..."
    }
  ]
}
"""


class IntelligenceAgent(BaseAgent):
    name = "IntelligenceAgent"
    system_prompt = _SYSTEM
    tool_schemas = INTELLIGENCE_TOOLS
    max_iterations = 10

    async def analyse_failures(self, failed_results: list[TestResult]) -> dict:
        if not failed_results:
            return {"executive_summary": "All tests passed. No failures to analyse.", "failure_groups": []}

        failures_text = "\n".join(
            f"- [{r.module}] {r.test_name}: {r.status} — {r.error or r.response_summary or ''}"
            for r in failed_results
        )

        prompt = (
            f"The following test cases failed during an AQE run:\n{failures_text}\n\n"
            "Query Qdrant for logs matching these failure patterns. "
            "Query Neo4j for module dependency relationships. "
            "Then produce the RCA JSON."
        )

        import json, re
        raw = await self.run(prompt)
        match = re.search(r'\{[\s\S]+\}', raw)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {"executive_summary": raw[:500], "failure_groups": []}

    async def generate_executive_summary(self, results: list[TestResult]) -> str:
        total   = len(results)
        passed  = sum(1 for r in results if r.status == TestStatus.PASSED)
        failed  = total - passed
        pct     = round(passed / total * 100, 1) if total else 0
        modules = list({r.module for r in results})

        prompt = (
            f"Write a 3-sentence executive summary for a banking API test run.\n"
            f"Stats: {total} tests, {passed} passed ({pct}%), {failed} failed.\n"
            f"Modules covered: {', '.join(modules)}.\n"
            f"Be concise, professional, and mention any critical failures."
        )

        response = self._BaseAgent__class_client().messages.create(  # type: ignore[attr-defined]
            model=self.model,
            max_tokens=512,
            system="You are a QA analyst writing brief executive summaries.",
            messages=[{"role": "user", "content": prompt}],
        ) if False else None

        # Simpler: use base run() without tools
        import anthropic
        from core.settings import settings
        client = anthropic.Anthropic(api_key=settings.claude_api_key)
        resp = client.messages.create(
            model=self.model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text if resp.content else ""
