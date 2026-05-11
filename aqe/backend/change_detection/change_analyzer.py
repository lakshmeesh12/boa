"""Claude-powered diff impact analyzer.

Takes a ChangeSet, sends a focused prompt to Claude, parses a strict JSON
response into a ChangeAnalysis. Results are cached on disk by HEAD SHA so
reopening AQE on the same code is instant.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import anthropic

from core.logging_config import get_logger
from core.settings import settings
from models.schemas import (
    BankingModule, ChangeAnalysis, ChangeSet, RiskLevel, SuggestedTest, TestCategory,
)

log = get_logger("ChangeAnalyzer")

_client = anthropic.Anthropic(api_key=settings.claude_api_key)

_CACHE_DIR = settings.data_dir / "change_cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Bound how much diff context we send Claude — we already truncated per-file.
_MAX_TOTAL_DIFF_CHARS = 60_000

_SYSTEM_PROMPT = """You are AQE's change-impact analyst.

Given a unified diff of a target system (a banking application: FastAPI + MongoDB + nginx + HTML/JS frontend), produce a tight, accurate analysis of what changed and what testing is needed.

You must output ONLY one JSON object — no prose before or after. Schema:

{
  "summary": "2-3 sentence description of what this change actually does",
  "modules_affected": ["CreditCards" | "Accounts" | "Customers" | "Deposits" | "Transactions" | "UI"],
  "risk_level": "low" | "medium" | "high" | "critical",
  "suggested_test_categories": ["Unit" | "Functional" | "API" | "Integration" | "Header" | "Security" | "Vulnerability" | "Performance" | "UI"],
  "suggested_new_tests": [
    {
      "name": "short title",
      "description": "one sentence",
      "category": "Unit | Functional | API | Integration | Header | Security | Vulnerability | Performance | UI",
      "module": "CreditCards | Accounts | Customers | Deposits | Transactions | UI",
      "rationale": "why this test, tied to a specific diff hunk",
      "method": "GET | POST | PUT | DELETE",   // for API/Functional/Security/Integration; omit otherwise
      "endpoint": "/api/v1/...",                // for API-style categories
      "payload": {} | null,                     // request body for POST/PUT
      "expected_status": 200 | 400 | 401 | 404, // expected HTTP status
      "ui_instruction": "..."                   // for UI category only — natural-language scenario
    }
  ],
  "detected_issues": [
    "concrete red flags you noticed in the diff — e.g. 'POST /limit-increase has no auth check', 'pip dependency requests==2.20.0 has known CVEs', 'os.system call in dispute handler is a command-injection risk'"
  ]
}

Rules:
- Be specific and reference actual lines/identifiers from the diff.
- Choose risk_level by the worst single issue: any auth bypass / RCE / SQLi / negative-amount logic = critical.
- Prefer 3-8 suggested_new_tests — quality over quantity.
- Categories: Vulnerability = SAST/SCA findings, Header = HTTP security headers, Performance = latency/load, Unit = function-level isolation, API = HTTP endpoint, UI = browser interaction.
- Output ONLY the JSON object. No markdown fences, no preamble."""


def _cache_path(head_sha: str) -> Path:
    return _CACHE_DIR / f"{head_sha}.json"


def _build_user_prompt(cs: ChangeSet) -> str:
    """Compose a compact prompt with file metadata + unified diffs."""
    header_lines = [
        f"Baseline: {cs.baseline_sha[:12]}  HEAD: {cs.head_sha[:12]}  Branch: {cs.branch}",
        f"Files changed: {len(cs.files)}  +{cs.total_additions}/-{cs.total_deletions}",
        "",
        "Changed files:",
    ]
    for f in cs.files:
        header_lines.append(f"  {f.status:10s} {f.path}  (+{f.additions}/-{f.deletions}, {f.language})")
    header_lines.append("")
    header_lines.append("Unified diffs:")
    header = "\n".join(header_lines)

    # Concatenate diffs, stopping when total length exceeds budget
    chunks: list[str] = []
    used = len(header)
    for f in cs.files:
        block = f"\n=== {f.path} ===\n{f.diff}\n"
        if used + len(block) > _MAX_TOTAL_DIFF_CHARS:
            chunks.append(f"\n=== {f.path} ===\n(diff omitted — prompt budget reached)\n")
            continue
        chunks.append(block)
        used += len(block)
    return header + "".join(chunks)


def _strip_json_fence(text: str) -> str:
    """Strip ```json ... ``` fences if Claude added them despite instructions."""
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    return m.group(1) if m else text


def _coerce_to_analysis(raw: dict, github_commit_url: str | None) -> ChangeAnalysis:
    """Map a raw dict from Claude into a strict ChangeAnalysis, tolerating loose inputs."""
    suggested_tests: list[SuggestedTest] = []
    for t in raw.get("suggested_new_tests") or []:
        if not isinstance(t, dict):
            continue
        try:
            cat = TestCategory(t.get("category", "API"))
        except ValueError:
            cat = TestCategory.API
        try:
            mod = BankingModule(t.get("module", "CreditCards"))
        except ValueError:
            mod = BankingModule.CREDIT_CARDS
        method = str(t.get("method", "GET")).upper()
        if method not in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
            method = "GET"
        try:
            expected_status = int(t.get("expected_status", 200))
        except (TypeError, ValueError):
            expected_status = 200
        payload = t.get("payload") if isinstance(t.get("payload"), dict) else None
        suggested_tests.append(SuggestedTest(
            name=str(t.get("name", "Untitled"))[:120],
            description=str(t.get("description", "")),
            category=cat,
            module=mod,
            rationale=str(t.get("rationale", "")),
            method=method,
            endpoint=str(t.get("endpoint", "")),
            payload=payload,
            expected_status=expected_status,
            ui_instruction=str(t.get("ui_instruction", "")),
        ))

    cats: list[TestCategory] = []
    for c in raw.get("suggested_test_categories") or []:
        try:
            cats.append(TestCategory(c))
        except ValueError:
            continue

    try:
        risk = RiskLevel(str(raw.get("risk_level", "low")).lower())
    except ValueError:
        risk = RiskLevel.LOW

    return ChangeAnalysis(
        summary=str(raw.get("summary", "")),
        modules_affected=[str(m) for m in (raw.get("modules_affected") or [])],
        risk_level=risk,
        suggested_test_categories=cats,
        suggested_new_tests=suggested_tests,
        detected_issues=[str(i) for i in (raw.get("detected_issues") or [])],
        github_commit_url=github_commit_url,
    )


class ChangeAnalyzer:
    def __init__(self, github_repo_url: str | None = None):
        # e.g. "https://github.com/lakshmeesh12/boa"
        self.github_repo_url = (github_repo_url or "").rstrip("/").removesuffix(".git")

    def _commit_url(self, head_sha: str) -> str | None:
        if not self.github_repo_url:
            return None
        return f"{self.github_repo_url}/commit/{head_sha}"

    async def analyze(self, cs: ChangeSet, *, use_cache: bool = True) -> ChangeAnalysis:
        """Analyze the given ChangeSet, returning a ChangeAnalysis.

        Empty ChangeSet → empty analysis (no Claude call).
        Cached by HEAD SHA — second call on the same SHA reads from disk.
        """
        if cs.is_empty:
            log.info("change_analyzer.empty_changeset")
            return ChangeAnalysis(
                summary="No changes detected since baseline.",
                risk_level=RiskLevel.LOW,
            )

        cache_file = _cache_path(cs.head_sha)
        if use_cache and cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                log.info("change_analyzer.cache_hit", context={"sha": cs.head_sha[:12]})
                return ChangeAnalysis(**data)
            except Exception as exc:
                log.warning("change_analyzer.cache_read_failed", context={"error": str(exc)})

        user_prompt = _build_user_prompt(cs)
        log.info(
            "change_analyzer.calling_claude",
            context={"sha": cs.head_sha[:12], "files": len(cs.files), "prompt_chars": len(user_prompt)},
        )

        response = _client.messages.create(
            model=settings.claude_model_sonnet,
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        text = next((b.text for b in response.content if hasattr(b, "text")), "").strip()
        text = _strip_json_fence(text)

        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            log.error(
                "change_analyzer.json_parse_failed",
                context={"error": str(exc), "preview": text[:300]},
            )
            return ChangeAnalysis(
                summary="Analysis failed — Claude returned non-JSON output.",
                risk_level=RiskLevel.MEDIUM,
                detected_issues=[f"Parse error: {exc}"],
                github_commit_url=self._commit_url(cs.head_sha),
            )

        analysis = _coerce_to_analysis(raw, self._commit_url(cs.head_sha))

        try:
            cache_file.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("change_analyzer.cache_write_failed", context={"error": str(exc)})

        log.info(
            "change_analyzer.done",
            context={
                "risk": analysis.risk_level,
                "modules": len(analysis.modules_affected),
                "new_tests": len(analysis.suggested_new_tests),
                "issues": len(analysis.detected_issues),
            },
        )
        return analysis
