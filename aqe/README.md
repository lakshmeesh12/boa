# AQE — Autonomous Quality Engineering Platform

Enterprise-grade AI testing framework for the Core Banking Simulator. Powered by Claude (Anthropic) with multi-agent orchestration, Computer Use for UI testing, GraphRAG (Qdrant + Neo4j) for intelligent log analysis, and a human-in-the-loop approval workflow.

---

## Quick Start

```powershell
# 1. Start the target banking simulator (if not already running)
cd c:\Users\Quadrant\boa
docker compose up -d

# 2. Install AQE dependencies
cd aqe
pip install -r requirements.txt
playwright install chromium

# 3. Start AQE (API :5001 + UI served at http://localhost:5001/)
python backend/start.py
```

Open **http://localhost:5001/** in your browser.

---

## What's inside

```
aqe/
├── backend/
│   ├── core/                   # Settings, structured logger, event bus (WebSocket pub/sub)
│   ├── orchestrator/
│   │   ├── engine.py           # Session state machine (PLANNING→AWAITING_APPROVAL→EXECUTING→COMPLETED)
│   │   ├── planner.py          # Discovery Agent → generates plan → waits for human approval
│   │   ├── session_store.py    # In-memory + JSON persistence
│   │   └── agents/
│   │       ├── discovery_agent.py    # Introspects target API, generates test plan
│   │       ├── execution_agent.py    # Runs API test suites via httpx
│   │       ├── ui_agent.py           # Computer Use + Playwright (claude-sonnet-4-6)
│   │       └── intelligence_agent.py # GraphRAG queries → Root Cause Analysis
│   ├── graphrag/
│   │   ├── qdrant_engine.py    # OpenAI embeddings + Qdrant vector store
│   │   ├── neo4j_engine.py     # Module dependency graph + blast-radius queries
│   │   └── log_ingestion.py    # Background log tail → embed → upsert
│   ├── test_suites/            # 42 declarative test cases across 5 banking modules
│   ├── test_runner/
│   │   ├── api_runner.py       # Async httpx test executor
│   │   ├── script_runner.py    # Subprocess runner for .sh/.py/Selenium scripts
│   │   ├── playwright_runner.py # Browser controller for Computer Use pipeline
│   │   └── report_generator.py # JSON + self-contained HTML reports
│   ├── routers/                # FastAPI endpoints (sessions, reports, scripts, graphrag, ws)
│   ├── tools/claude_tools.py   # Claude tool schemas + executor dispatch
│   ├── models/schemas.py       # All Pydantic models
│   └── main.py                 # FastAPI app + StaticFiles mount for frontend
└── frontend/                   # Dark-theme SPA (6 views, WebSocket live feed)
    ├── index.html
    └── app.js
```

---

## UI Views

| View | What you see |
|---|---|
| **Dashboard** | System health (AQE / Target API / Qdrant / Neo4j), active sessions, recent reports |
| **New Session** | 3-step wizard: configure modules + upload scripts → AI plan review + chat → approve/reject |
| **Live Execution** | Dual-panel: agent activity log + terminal output, progress bar, human-in-loop clarification |
| **Reports** | Sortable report list, inline HTML viewer with per-test RCA |
| **GraphRAG** | Qdrant semantic search over API logs, Neo4j D3 dependency graph |
| **Settings** | Read-only display of active configuration |

---

## Human-in-the-loop

The AQE never auto-executes destructive tests. Every session goes through:

1. **Plan review** — AI generates a prioritised test list; you read it, ask questions via chat, then click **Approve** or **Reject with feedback**.
2. **Mid-execution pause** — If the execution agent hits ambiguity, it emits a `needs_clarification` event. The live view shows the question with an input box; your reply resumes execution immediately.

---

## Uploading test scripts

On the New Session page, drag-and-drop or browse for:

- `.sh` / `.bash` — executed via `bash script.sh`
- `.py` — executed via `python script.py` (Selenium scripts also work; Chrome is launched automatically)
- `.feature` — executed via `python -m behave`

`TARGET_API_URL`, `TARGET_UI_URL`, and `SELENIUM_TARGET` are injected as environment variables so scripts can reference them without hard-coding.

---

## Environment (.env)

All credentials come from `boa/.env` (the root file shared with the banking simulator):

```env
CLAUDE_API_KEY=sk-ant-api03-...
OPENAI_API_KEY=sk-proj-...
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=
QDRANT_COLLECTION_LOGS=boa_logs      # (override for AQE logs; default: boa_logs)
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=test12345
TARGET_API_URL=http://localhost:8000
TARGET_UI_URL=http://localhost:8080
AQE_API_PORT=5001
```

---

## API reference

- `GET  /health` — system health check
- `POST /api/v1/sessions` — create session (triggers plan generation)
- `GET  /api/v1/sessions` — list sessions
- `POST /api/v1/sessions/{id}/approve` — approve plan → start execution
- `POST /api/v1/sessions/{id}/reject` — reject with feedback
- `POST /api/v1/sessions/{id}/clarify` — send reply to paused agent
- `WS   /api/v1/sessions/{id}/ws` — live event stream
- `GET  /api/v1/reports` — list reports
- `GET  /api/v1/reports/{id}/html` — HTML report
- `POST /api/v1/sessions/{id}/scripts/upload` — upload test script
- `POST /api/v1/graphrag/search` — semantic log search
- `GET  /api/v1/graphrag/graph` — Neo4j module graph data
- Full interactive docs at **http://localhost:5001/docs**
