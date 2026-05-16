# AQE Cucumber Demo — Credit Limit Increase

A self-contained Cucumber/Gherkin suite that exercises the BOA banking
simulator's **Credit Limit Increase** API. It demonstrates how AQE ingests
test suites directly from a GitHub repo via the **Source Connections** feature
on the Settings page.

## What's in here

```
features/
  happy_path.feature            — positive-delta happy path
  idempotency.feature           — state carryover across two POSTs
  validation.feature            — negative-delta rejection contract
  steps/
    credit_cards_steps.py       — Python step definitions (shared)
  environment.py                — Behave hooks (before_all, before_scenario)
requirements.txt                — behave + requests
```

Each feature file isolates one concern so the AQE library shows three
distinct entries after sync. The three scenarios are designed for a
memorable demo:

1. **happy_path.feature** — POST +1500, asserts HTTP 200 + response shape + delta arithmetic. **Expected to PASS**.
2. **idempotency.feature** — two sequential POSTs (+500 then +250) with state-carryover check. **Expected to PASS**.
3. **validation.feature** — POSTs -1000 and asserts the API rejects it with 4xx. **Expected to FAIL** on the current BOA build (the API accepts negative deltas — AQE's Supervisor classifies this as a REAL_BUG in the report).

## How AQE uses it

1. Push this directory to a GitHub repo of your choice (e.g. `lakshmeesh12/aqe-cucumber-demo`).
2. In AQE → **Settings** → click into the **CreditCards** module.
3. Click **+ Add source**. Pick the GitHub connection. Pick the repo. Pick the branch. Leave the path empty (sync the full repo).
4. AQE clones the repo, copies the files into the credit_cards library folder, and registers `limit_increase.feature` as a synced library entry.
5. AQE detects `requirements.txt`. Open the **Dependencies** panel on the source row → **Install dependencies**. AQE creates a per-source venv at `aqe/data/source_venvs/<source_id>/` and pip-installs `behave + requests` there.
6. Trigger a Changes-tab run in **Pre-built only** mode with this feature file selected. AQE runs `behave --format=json` inside the per-source venv. The HTML report shows **three scenario-level rows** under "credit_cards module", grouped by origin = PRE_BUILT.

## Environment variables consumed

The step definitions and `environment.py` read AQE-injected env vars:

| Variable                  | Used by                          |
|---------------------------|----------------------------------|
| `TARGET_API_URL`          | step_target_reachable, step_pick_active_card, _post_limit_change |
| `TARGET_API_AUTH_TYPE`    | `_headers()` in steps            |
| `TARGET_API_TOKEN`        | `_headers()` in steps (when auth ≠ none) |

These are populated by `aqe/backend/test_runner/script_runner.py::_build_env`
using the **module's pinned target** (Settings → CreditCards → Target environment)
or the session-level target if no module override is set.

## Running locally (sanity check before pushing)

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
$env:TARGET_API_URL = "http://localhost:8000"
.venv\Scripts\behave features/
```

The third scenario will fail when the BOA target is in the
`push_feature_credit_limit_increase.ps1` state because the negative-delta
validation is missing server-side — that's the point of the demo.
