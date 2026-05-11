# BOA — Banking Simulator + AQE

A two-part system for demonstrating change-aware, AI-driven quality engineering:

- **`backend/`, `frontend/`, `infra/`** — the BOA core-banking target (FastAPI + MongoDB + nginx), runs in Docker. Seeded with intentional security and logic vulnerabilities for AQE to discover.
- **`aqe/`** — the AQE platform itself: a Claude-powered orchestrator that detects target code changes, plans test runs, executes API + UI + Unit + Security + Performance + Vulnerability tests, streams the UI agent live, and produces category-wise reports.

```powershell
# Bring up the BOA target
docker compose up -d
# Start AQE
cd aqe\backend; python start.py
# Open http://localhost:5001
```

Demo loop: `demo_scripts\setup_demo_repo.ps1` (one-time) → `demo_scripts\push_feature_credit_limit_increase.ps1` → open AQE → `demo_scripts\revert_to_baseline.ps1` to reset.

---

## Target Banking Simulator

A POC core-banking environment for an external Autonomous Quality Engineering
(AQE) framework to drive: API tests via Bash/`curl`, UI tests via Selenium,
log analysis via the JSON log stream.

## Stack

| Service | Port  | Purpose                                                 |
|---------|-------|---------------------------------------------------------|
| `mongo` | 27017 | Mongo 7.0 single-node replica set (`rs0`) — supports multi-doc transactions |
| `api`   | 8000  | FastAPI core-banking API (`/api/v1/...`, `/health`, `/docs`) |
| `ui`    | 8080  | Static "Banking Agent Portal" (HTML + Tailwind via CDN), nginx proxies `/api/*` to the API |

All API logs are written as **strict-JSON, one record per line** to:

- `stdout` (visible via `docker compose logs api`)
- `/var/logs/bank-simulator/api.log` inside the shared `bank_logs` volume

## Run

```bash
docker compose up --build
```

On first start the seeder populates 50 customers + linked accounts, credit
cards and fixed deposits, plus 2 deterministic edge-case customers
(BLOCKED card, FROZEN account / REJECTED KYC). Re-running is idempotent —
to re-seed, drop the volume:

```bash
docker compose down -v && docker compose up --build
```

## Endpoints (highlights)

- `GET  /health`
- `GET  /api/v1/customers/{customer_id}/portfolio`
- `GET  /api/v1/credit-cards/{card_id}`
- `POST /api/v1/credit-cards/{card_id}/block`            `{ "reason": "..." }`
- `GET  /api/v1/fixed-deposits/{deposit_id}`
- `POST /api/v1/fixed-deposits/simulate-maturity`        `{ principal_amount, interest_rate_apy, tenure_months }`
- `POST /api/v1/transactions/execute`                    `{ source_id, entity_type, type, amount, ... }` — atomic via `session.start_transaction()`

Interactive docs at `http://localhost:8000/docs`.

## Discovering test fixtures

To find seeded IDs the AQE framework can target:

```bash
docker compose exec mongo mongosh core_banking --quiet \
  --eval 'db.seed_summary.findOne()'

docker compose exec mongo mongosh core_banking --quiet \
  --eval 'db.customers.find({}, {_id:1}).limit(5).toArray()'
```

## Selenium-friendly IDs

Every interactive element on the UI carries both `id="..."` and
`data-test-id="..."`. Examples:
`input-customer-search`, `btn-customer-search`,
`btn-block-card`, `input-block-reason`,
`btn-fd-calculate`, `fd-result-payout`,
`portfolio-customer-name`, `card-status-pill`.

## Log shape (for the AI parser)

```json
{
  "timestamp": "2026-05-09T13:24:11.084+00:00",
  "level": "WARNING",
  "service": "CoreBankingAPI",
  "trace_id": "9c1a…",
  "span_id": "a1b2c3d4",
  "module": "CreditCardService",
  "message": "security.credit_card.blocked",
  "context": { "event": "CARD_BLOCKED", "card_id": "65f…", "masked": "XXXX-XXXX-XXXX-1234", "reason": "lost in transit" }
}
```

PII (names, full PAN, email, phone) is scrubbed before logging.
