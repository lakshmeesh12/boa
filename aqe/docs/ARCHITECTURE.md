# AQE Platform — Architecture & Design Documentation

## Overview

The **Autonomous Quality Engineering (AQE) Platform** is an enterprise-grade, AI-driven testing framework built on top of a live Core Banking Simulator. It autonomously discovers, plans, executes, and reports on test scenarios — with a human-in-the-loop approval gate and GraphRAG-powered root-cause analysis.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          AQE PLATFORM  (localhost:5001)                     │
│                                                                             │
│  ┌──────────────┐    ┌───────────────────────────────────────────────────┐  │
│  │              │    │                ORCHESTRATION ENGINE                │  │
│  │   React-less │    │                                                   │  │
│  │   SPA (UI)   │◄──►│  ┌──────────────┐  ┌─────────────────────────┐  │  │
│  │  port :5001  │    │  │DiscoveryAgent│  │  ExecutionAgent (httpx)  │  │  │
│  │   (6 views)  │    │  │  (Claude API)│  │  ScriptRunner (subprocess│  │  │
│  └──────┬───────┘    │  └──────────────┘  └─────────────────────────┘  │  │
│         │ WebSocket  │  ┌──────────────┐  ┌─────────────────────────┐  │  │
│         │ + REST     │  │  UI Agent    │  │  Intelligence Agent      │  │  │
│         │            │  │ (Computer Use│  │  (GraphRAG → RCA)        │  │  │
│         │            │  │ + Playwright) │  └─────────────────────────┘  │  │
│  ┌──────▼───────┐    │  └──────────────┘                                │  │
│  │  FastAPI     │    │  ┌──────────────────────────────────────────┐    │  │
│  │  Backend     │    │  │  Planner (human-in-loop approval gate)   │    │  │
│  │  (uvicorn)   │    │  └──────────────────────────────────────────┘    │  │
│  └──────────────┘    └───────────────────────────────────────────────────┘  │
│                                          │                                  │
│                      ┌───────────────────┼───────────────────┐              │
│                      ▼                   ▼                   ▼              │
│              ┌──────────────┐   ┌──────────────┐   ┌──────────────┐        │
│              │   Qdrant     │   │    Neo4j     │   │ Session Store│        │
│              │ Vector Store │   │ Graph Store  │   │ (JSON + RAM) │        │
│              │ localhost:   │   │ localhost:   │   │              │        │
│              │ 6333         │   │ 7687         │   │              │        │
│              └──────────────┘   └──────────────┘   └──────────────┘        │
└─────────────────────────────────────────────────────────────────────────────┘
                                   │  API tests
                                   │  UI tests (Playwright)
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│              TARGET BANKING SIMULATOR  (Docker Compose)                     │
│                                                                             │
│  ┌──────────────────────┐    ┌───────────────────────────────────────────┐  │
│  │  FastAPI Core API    │    │    Static UI (nginx)                      │  │
│  │  port :8000          │    │    port :8080                             │  │
│  │                      │    │                                           │  │
│  │  /api/v1/customers   │    │  Dashboard · Credit Cards · Deposits      │  │
│  │  /api/v1/accounts    │    │  (Tailwind · Vanilla JS · SPA)            │  │
│  │  /api/v1/credit-cards│    └───────────────────────────────────────────┘  │
│  │  /api/v1/deposits    │                                                   │
│  │  /api/v1/transactions│    ┌───────────────────────────────────────────┐  │
│  │  /api/v1/audit-log   │    │    MongoDB 7.0 (replica set rs0)          │  │
│  └──────────────────────┘    │    port :27017 · core_banking database    │  │
│                              │    50 seeded customers + accounts/cards/FD │  │
│                              └───────────────────────────────────────────┘  │
│                                                                             │
│  Logs → /var/logs/bank-simulator/api.log (Docker volume: bank_logs)         │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Session Lifecycle (State Machine)

```
 ┌────────────────────────────────────────────────────────────────────┐
 │                                                                    │
 │   User creates session                                             │
 │         │                                                          │
 │         ▼                                                          │
 │   ┌───────────┐     Discovery Agent introspects target             │
 │   │ PLANNING  │──►  Claude generates test plan JSON                │
 │   └─────┬─────┘                                                    │
 │         │                                                          │
 │         ▼                                                          │
 │   ┌──────────────────────┐                                         │
 │   │  AWAITING_APPROVAL   │◄── Plan shown in UI                     │
 │   └──────────┬───────────┘    User can: Approve / Reject / Chat    │
 │              │                                                     │
 │     Approve  │   Reject                                            │
 │              │──────────────────────────────────►  back to IDLE   │
 │              ▼                                                     │
 │   ┌────────────────────┐                                           │
 │   │     EXECUTING      │──► API tests → UI tests → Script tests    │
 │   └────────┬───────────┘                                           │
 │            │                                                       │
 │            │  (agent needs info)                                   │
 │            ├──────────────────────────────────────────────────►   │
 │            │                ┌────────────────────────┐            │
 │            │                │  WAITING_FOR_INPUT     │            │
 │            │                │  (clarification panel  │            │
 │            │                │   shown in UI)         │            │
 │            │                └───────────┬────────────┘            │
 │            │◄──────────────────────────┘  user replies            │
 │            │                                                       │
 │            ▼                                                       │
 │   Intelligence Agent queries GraphRAG → RCA                       │
 │            │                                                       │
 │            ▼                                                       │
 │   ┌────────────────────┐                                           │
 │   │     COMPLETED      │──► Report generated (JSON + HTML)         │
 │   └────────────────────┘                                           │
 │                                                                    │
 │   (failure at any step → FAILED)                                   │
 │   (user clicks cancel → CANCELLED)                                 │
 └────────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Component | Technology | Version | Why |
|---|---|---|---|
| **AQE Backend API** | FastAPI + Uvicorn | 0.115 / 0.32 | Async-native, WebSocket support, auto OpenAPI docs |
| **Agent Brain** | Anthropic Claude Opus 4.7 | `claude-opus-4-7` | Best reasoning for test planning and RCA |
| **UI Test Agent** | Claude Sonnet 4.6 + Computer Use β | `claude-sonnet-4-6` | Computer Use API requires Sonnet |
| **Browser Controller** | Playwright (Chromium, headless) | 1.49 | Async API, reliable screenshot pipeline |
| **Embeddings** | OpenAI `text-embedding-3-large` | dim=3072 | Highest-quality embeddings for log semantic search |
| **Vector Store** | Qdrant | 1.12 | High-performance cosine similarity search, Python-native client |
| **Graph Store** | Neo4j | 5.x | Cypher query language, DEPENDS_ON blast-radius traversal |
| **HTTP Client** | httpx | 0.27 | Async, cleaner than aiohttp for test assertions |
| **Frontend** | Plain HTML + Tailwind CDN + Vanilla JS | — | No build step, fast iteration, Selenium-friendly `data-test-id` attributes |
| **Target: API** | FastAPI + Motor (async Mongo driver) | — | Async, BSON Decimal128 for currency precision |
| **Target: DB** | MongoDB 7.0 (replica set rs0) | — | Multi-document ACID transactions for ledger operations |
| **Target: UI** | nginx + static HTML/Tailwind | 1.27-alpine | Lightweight, serves from Docker volume |
| **Containerisation** | Docker Compose | v3.9 | Target machine only; AQE runs natively (no container) |

---

## Component Deep-Dives

### 1. Orchestration Engine (`orchestrator/engine.py`)

The engine owns the session state machine. It:
- Spawns a background `asyncio.Task` per session
- Sequences agents: Discovery → Execution → UI → Scripts → Intelligence
- Publishes all events to the in-process event bus (WebSocket fan-out)
- Persists session state to disk after every mutation (crash recovery)

### 2. Discovery Agent (`orchestrator/agents/discovery_agent.py`)

- Model: `claude-opus-4-7`
- Tools available: `run_api_test` (probes `/health` and sample endpoints)
- Output: JSON test plan `{ ai_summary, test_cases[] }`
- The plan is MERGED with built-in declarative suites (42 pre-written cases)
- AI-generated cases fill gaps; built-in cases provide correctness guarantees

### 3. Execution Agent (`orchestrator/agents/execution_agent.py`)

A thin wrapper around `APIRunner` (httpx-based). For each `TestCase`:
- Resolves fixture placeholders (`{account_id}`, `{card_id}`, etc.) from live DB
- Sends the HTTP request with the correct method/payload
- Validates: status code + required response fields + security invariants
- Returns `TestResult(status, duration_ms, trace_id, error)`

### 4. UI Agent (`orchestrator/agents/ui_agent.py`)

Implements the **Computer Use agentic loop**:

```
Navigate to UI → Take screenshot (Playwright)
      │
      ▼
Claude Sonnet (computer-use-2025-01-24 beta)
      │ sees screenshot, returns action blocks
      ▼
Playwright executes: click(x,y) / type(text) / scroll(dir)
      │
      ▼
New screenshot → back to Claude
      │
      ▼ (when scenario is assessed)
record_test_result tool call → TestResult
```

Claude navigates the banking portal visually, without hard-coded selectors.

### 5. Intelligence Agent (`orchestrator/agents/intelligence_agent.py`)

Post-execution only. For failed tests:
1. Calls `query_qdrant`: embeds failure message → finds similar historical log entries
2. Calls `query_neo4j`: blast-radius query — which modules DEPEND_ON the failing module
3. Returns structured RCA: `{ executive_summary, failure_groups[{ common_cause, blast_radius, remediation }] }`
4. RCA text is attached to each `TestResult` and rendered in the HTML report

### 6. GraphRAG Pipeline

```
Bank API logs (JSON, one line per record)
         │
         ▼
log_ingestion.py  ←──── asyncio background task (polls every 15s)
         │    docker exec bank-api tail -n 200 /var/logs/bank-simulator/api.log
         │                  OR  reads /var/logs/bank-simulator/api.log directly
         ▼
OpenAI text-embedding-3-large  (dim=3072)
         │
         ├──► Qdrant  upsert(vector, payload{level,module,message,trace_id})
         │
         └──► Neo4j  (level≥ERROR only)
              MERGE (:Error {trace_id}) ← linked to failing TestCase
```

**Qdrant** answers: "Have we seen this error before?" (cosine similarity search)
**Neo4j** answers: "If Transactions module fails, what else is at risk?" (graph traversal)

### 7. Script Runner (`test_runner/script_runner.py`)

Executes user-uploaded test scripts as subprocesses:

| File type | Interpreter | Extra env vars injected |
|---|---|---|
| `.sh` / `.bash` | `bash` | `TARGET_API_URL`, `TARGET_UI_URL` |
| `.py` (generic) | `python` | `TARGET_API_URL`, `TARGET_UI_URL`, `AQE_RUN=1` |
| `.py` (selenium) | `python` | + `SELENIUM_TARGET=http://localhost:8080` |
| `.feature` | `python -m behave` | All of the above |

Output is streamed line-by-line to the WebSocket live terminal. Exit code 0 = PASS.

---

## Data Flow: End-to-End Test Run

```
1. User → POST /api/v1/sessions  { modules, test_types, name }
2. Engine → start_planning()     background task
3. DiscoveryAgent → Claude API   probe target, generate plan JSON
4. Planner → emit_plan()         WebSocket: "plan_ready" event → UI
5. User → POST /sessions/{id}/approve
6. Engine → _run_api_tests()
7.    APIRunner → httpx → Target API (:8000)
8.    TestResult → event_bus.emit_test_result() → WS → live terminal
9.    Neo4j record_test_result() for each result
10. Engine → _run_ui_tests()     (if UI module selected)
11.   UIAgent → Playwright navigate(:8080)
12.   UIAgent → Claude (Computer Use) → action blocks
13.   Playwright execute action → screenshot → back to Claude
14.   record_test_result tool → TestResult
15. Engine → _run_script_tests()  (if scripts uploaded)
16.   ScriptRunner → subprocess → stream output → WS live terminal
17. IntelligenceAgent → failed tests → Qdrant search + Neo4j blast-radius
18. Report = generate(session) → save JSON + HTML
19. emit_report_ready() → WS → UI shows "View Report" banner
```

---

## Neo4j Graph Schema

```
Nodes:
  (:Module  { name })           -- Customers, Accounts, CreditCards, Deposits, Transactions, UI
  (:TestRun { id, session_id, started_at, status })
  (:TestCase{ id, name, status, duration_ms })
  (:Error   { trace_id, message, level, timestamp })

Relationships:
  (:TestCase)-[:PART_OF]->(:TestRun)
  (:TestCase)-[:TESTS]->(:Module)
  (:TestCase)-[:PRODUCED_ERROR]->(:Error)
  (:Module)-[:DEPENDS_ON]->(:Module)

Static DEPENDS_ON graph:
  Transactions ──► Accounts
  Transactions ──► CreditCards
  Transactions ──► Deposits
  Deposits     ──► Accounts
  Deposits     ──► Customers
  CreditCards  ──► Customers
  Accounts     ──► Customers
```

Blast-radius query example:
```cypher
MATCH (m:Module)-[:DEPENDS_ON*1..3]->(dep:Module {name: 'Accounts'})
RETURN DISTINCT m.name AS affected_module
-- Returns: Transactions, Deposits
```

---

## API Reference (AQE Backend)

| Method | Path | Description |
|---|---|---|
| GET  | `/health` | System health (AQE + target + Qdrant + Neo4j) |
| POST | `/api/v1/sessions` | Create session → triggers plan generation |
| GET  | `/api/v1/sessions` | List all sessions |
| GET  | `/api/v1/sessions/{id}` | Get session detail + results |
| POST | `/api/v1/sessions/{id}/approve` | Approve plan → start execution |
| POST | `/api/v1/sessions/{id}/reject` | Reject with feedback |
| POST | `/api/v1/sessions/{id}/clarify` | Send reply to paused agent |
| DELETE | `/api/v1/sessions/{id}` | Cancel session |
| WS   | `/api/v1/sessions/{id}/ws` | Live event stream |
| POST | `/api/v1/sessions/{id}/scripts/upload` | Upload test script |
| GET  | `/api/v1/reports` | List reports |
| GET  | `/api/v1/reports/{id}` | Report JSON |
| GET  | `/api/v1/reports/{id}/html` | Self-contained HTML report |
| POST | `/api/v1/graphrag/ingest` | Trigger log ingestion |
| POST | `/api/v1/graphrag/search` | Semantic log search |
| GET  | `/api/v1/graphrag/graph` | Neo4j graph data (D3 format) |
| GET  | `/docs` | Interactive Swagger UI |

---

## Target Machine API (Core Banking Simulator)

| Method | Path | AQE Test Focus |
|---|---|---|
| GET | `/api/v1/customers` | Pagination, KYC filtering |
| GET | `/api/v1/customers/{id}/portfolio` | Aggregation, edge cases |
| GET | `/api/v1/accounts/{id}` | Single-entity retrieval |
| GET | `/api/v1/accounts/{id}/statement` | Ledger read |
| GET | `/api/v1/credit-cards` | Status filtering |
| GET | `/api/v1/credit-cards/{id}` | Security (hash not leaked) |
| POST| `/api/v1/credit-cards/{id}/block` | State mutation, 400/409 |
| GET | `/api/v1/fixed-deposits` | List with customer join |
| POST| `/api/v1/fixed-deposits/simulate-maturity` | Math validation |
| POST| `/api/v1/transactions/execute` | Atomicity, solvency, idempotency |
| GET | `/api/v1/audit-log?trace_id=X` | Log correlation |

---

## Security Design

| Concern | Implementation |
|---|---|
| PII in logs | Banking API scrubs names, emails, phone via regex before logging |
| Card numbers | `card_number_hash` (SHA-256) never returned by API; `card_number_masked` only |
| Currency precision | MongoDB `Decimal128` everywhere; no IEEE-754 floats |
| Atomic transactions | MongoDB multi-document session (`session.start_transaction()`) |
| AQE secrets | API keys in `.env`, never transmitted to browser |
| Script execution | Subprocess with restricted env; no shell injection (paths validated, extension whitelist) |
| CORS | AQE allows all origins (POC); restrict in production |

---

## File Structure

```
aqe/
├── docs/
│   └── ARCHITECTURE.md               ← this file
├── requirements.txt
├── README.md
├── backend/
│   ├── start.py                      entry point (uvicorn)
│   ├── main.py                       FastAPI app factory + SPA catch-all
│   ├── core/
│   │   ├── settings.py               all env config (reads root .env)
│   │   ├── logging_config.py         structured JSON logger
│   │   └── event_bus.py              asyncio pub/sub → WebSocket fan-out
│   ├── models/schemas.py             Pydantic: Session, Plan, TestCase, TestResult, Report
│   ├── orchestrator/
│   │   ├── engine.py                 state machine, agent dispatch, report trigger
│   │   ├── planner.py                discovery → plan → AWAITING_APPROVAL
│   │   ├── session_store.py          RAM + JSON disk persistence
│   │   └── agents/
│   │       ├── base_agent.py         Anthropic streaming tool-use loop
│   │       ├── discovery_agent.py    Claude → JSON test plan
│   │       ├── execution_agent.py    wraps APIRunner
│   │       ├── ui_agent.py           Computer Use + Playwright loop
│   │       └── intelligence_agent.py GraphRAG → RCA
│   ├── graphrag/
│   │   ├── qdrant_engine.py          OpenAI embed + upsert + cosine search
│   │   ├── neo4j_engine.py           Cypher CRUD + blast-radius query
│   │   └── log_ingestion.py          background tail → embed → store
│   ├── test_runner/
│   │   ├── api_runner.py             httpx async test executor + fixture resolver
│   │   ├── script_runner.py          subprocess bash/python/selenium
│   │   ├── playwright_runner.py      browser screenshot + action executor
│   │   └── report_generator.py       JSON + standalone HTML report
│   ├── test_suites/
│   │   ├── base.py                   registry + get_suite()
│   │   ├── customers_suite.py        8 test cases
│   │   ├── accounts_suite.py         6 test cases
│   │   ├── credit_cards_suite.py     10 test cases
│   │   ├── deposits_suite.py         8 test cases
│   │   └── transactions_suite.py     10 test cases (42 total)
│   ├── tools/claude_tools.py         tool schemas + ToolExecutor dispatch
│   └── routers/
│       ├── sessions.py               CRUD + approve/reject/clarify
│       ├── reports.py                list + JSON + HTML
│       ├── graphrag.py               ingest + search + graph
│       ├── stream.py                 WebSocket per session
│       └── scripts.py               upload + toggle + delete
└── frontend/
    ├── index.html                    6-view SPA shell
    └── app.js                        routing, WebSocket client, D3 graph, live terminal
```
