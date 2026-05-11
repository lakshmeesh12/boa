"""AQE Pydantic models — sessions, plans, test results, reports, scripts."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ─── Enums ───────────────────────────────────────────────────────────────
class SessionState(str, Enum):
    IDLE             = "IDLE"
    PLANNING         = "PLANNING"
    AWAITING_APPROVAL= "AWAITING_APPROVAL"
    EXECUTING        = "EXECUTING"
    WAITING_FOR_INPUT= "WAITING_FOR_INPUT"
    COMPLETED        = "COMPLETED"
    FAILED           = "FAILED"
    CANCELLED        = "CANCELLED"


class TestStatus(str, Enum):
    PENDING  = "PENDING"
    RUNNING  = "RUNNING"
    PASSED   = "PASSED"
    FAILED   = "FAILED"
    ERROR    = "ERROR"
    SKIPPED  = "SKIPPED"


class BankingModule(str, Enum):
    CUSTOMERS     = "Customers"
    ACCOUNTS      = "Accounts"
    CREDIT_CARDS  = "CreditCards"
    DEPOSITS      = "Deposits"
    TRANSACTIONS  = "Transactions"
    UI            = "UI"
    CUSTOM_SCRIPT = "CustomScript"


class TestType(str, Enum):
    FUNCTIONAL  = "Functional"
    EDGE_CASE   = "EdgeCase"
    SECURITY    = "Security"
    ALL         = "All"


class TestCategory(str, Enum):
    """Broader test category dimension (orthogonal to TestType).

    A single TestCase has one category; the report groups by both module and category.
    """
    UNIT          = "Unit"
    FUNCTIONAL    = "Functional"
    API           = "API"
    INTEGRATION   = "Integration"
    HEADER        = "Header"
    SECURITY      = "Security"
    VULNERABILITY = "Vulnerability"
    PERFORMANCE   = "Performance"
    UI            = "UI"


class PlanMode(str, Enum):
    SMART     = "smart"       # impacted existing tests + new tests for the diff
    FULL      = "full"        # all existing tests + new tests
    NEW_ONLY  = "new_only"    # only new tests Claude generated for this diff


class RiskLevel(str, Enum):
    LOW       = "low"
    MEDIUM    = "medium"
    HIGH      = "high"
    CRITICAL  = "critical"


class ScriptType(str, Enum):
    BASH     = "bash"
    PYTHON   = "python"
    SELENIUM = "selenium"
    FEATURE  = "feature"   # Cucumber/Gherkin


# ─── Test Primitives ─────────────────────────────────────────────────────
class TestCase(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str
    module: BankingModule
    test_type: TestType = TestType.FUNCTIONAL
    category: TestCategory = TestCategory.API
    method: str = "GET"
    endpoint: str = ""
    payload: dict | None = None
    expected_status: int = 200
    expected_fields: list[str] = Field(default_factory=list)
    # For script-based tests
    script_path: str | None = None
    script_type: ScriptType | None = None
    # For change-driven tests — links back to the diff that prompted this case
    triggered_by_files: list[str] = Field(default_factory=list)


class TestResult(BaseModel):
    test_id: str
    test_name: str
    module: str
    category: str = "API"
    status: TestStatus
    duration_ms: float
    request_summary: str = ""
    response_summary: str = ""
    error: str | None = None
    rca: str | None = None        # Root cause analysis from Intelligence Agent
    trace_id: str | None = None
    severity: str | None = None   # for vulnerability findings: critical / high / medium / low
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ─── Plan ────────────────────────────────────────────────────────────────
class PlanItem(BaseModel):
    test_case: TestCase
    rationale: str = ""
    order: int = 0


class Plan(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    items: list[PlanItem] = Field(default_factory=list)
    ai_summary: str = ""
    total_cases: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ─── Chat / Human-in-the-loop ────────────────────────────────────────────
class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ─── Uploaded Scripts ────────────────────────────────────────────────────
class UploadedScript(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    filename: str
    script_type: ScriptType
    file_path: str
    size_bytes: int
    enabled: bool = True
    ai_summary: str = ""
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ─── Change Detection ────────────────────────────────────────────────────
class ChangedFile(BaseModel):
    path: str                        # repo-relative, e.g. "backend/routers/credit_card_services.py"
    status: str                      # "added" | "modified" | "deleted" | "renamed"
    language: str = "unknown"        # "python" | "javascript" | "html" | "yaml" | ...
    additions: int = 0
    deletions: int = 0
    diff: str = ""                   # unified-diff text for this file (capped in size)


class ChangeSet(BaseModel):
    baseline_sha: str
    baseline_tag: str = "aqe-demo-baseline"
    head_sha: str
    branch: str = "main"
    files: list[ChangedFile] = Field(default_factory=list)
    total_additions: int = 0
    total_deletions: int = 0
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_empty(self) -> bool:
        return len(self.files) == 0

    @property
    def file_count(self) -> int:
        return len(self.files)


class SuggestedTest(BaseModel):
    name: str
    description: str
    category: TestCategory
    module: BankingModule
    rationale: str = ""              # why Claude wants this test
    # Optional execution hints — populated for API/Functional/Security/Integration categories.
    # Unit / Vulnerability / Performance / Header / UI categories use runner-defined logic
    # and may leave these blank.
    method: str = "GET"
    endpoint: str = ""
    payload: dict | None = None
    expected_status: int = 200
    ui_instruction: str = ""          # natural-language scenario for UI category


class ChangeAnalysis(BaseModel):
    summary: str = ""                                 # 2-3 sentence overview
    modules_affected: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    suggested_test_categories: list[TestCategory] = Field(default_factory=list)
    suggested_new_tests: list[SuggestedTest] = Field(default_factory=list)
    detected_issues: list[str] = Field(default_factory=list)   # red flags
    github_commit_url: str | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChangeContext(BaseModel):
    """Bundled ChangeSet + ChangeAnalysis — passed to planner when a session is change-driven."""
    change_set: ChangeSet
    analysis: ChangeAnalysis


# ─── Session ─────────────────────────────────────────────────────────────
class Session(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    state: SessionState = SessionState.IDLE
    modules: list[BankingModule] = Field(default_factory=list)
    test_types: list[TestType] = Field(default_factory=list)
    plan_mode: PlanMode = PlanMode.FULL
    change_context: ChangeContext | None = None
    plan: Plan | None = None
    results: list[TestResult] = Field(default_factory=list)
    uploaded_scripts: list[UploadedScript] = Field(default_factory=list)
    chat_history: list[ChatMessage] = Field(default_factory=list)
    clarification_question: str | None = None
    report_id: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.PASSED)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.FAILED)

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.ERROR)


# ─── Report ──────────────────────────────────────────────────────────────
class Report(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    session_name: str
    modules_tested: list[str]
    total: int
    passed: int
    failed: int
    errors: int
    duration_seconds: float
    results: list[TestResult]
    ai_executive_summary: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ─── API Request Bodies ───────────────────────────────────────────────────
class CreateSessionRequest(BaseModel):
    name: str = Field(default="", max_length=100)
    modules: list[BankingModule]
    test_types: list[TestType] = Field(default_factory=lambda: [TestType.ALL])
    plan_mode: PlanMode = PlanMode.FULL
    use_change_context: bool = False  # if True, planner picks up current ChangeSet from git_watcher


class ApproveRequest(BaseModel):
    message: str = "approved"


class RejectRequest(BaseModel):
    feedback: str = Field(..., min_length=3)


class ClarifyRequest(BaseModel):
    message: str = Field(..., min_length=1)


class GraphRAGQueryRequest(BaseModel):
    query: str = Field(..., min_length=3)
    limit: int = Field(default=5, ge=1, le=20)
