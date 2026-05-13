# ============================================================================
# push_feature_credit_limit_increase.ps1
#
# Simulates a developer pushing a multi-file "feature" to the BOA target.
# Each mutation is designed to trigger exactly one AQE test category so the
# resulting Smart/Full/NewOnly run exercises the full pipeline:
#
#   #1 New endpoint POST /credit-cards/{id}/limit-increase  -> API + Functional + Security
#   #2 import os + os.system in dispute handler             -> Vulnerability (SAST)
#   #3 _calculate_late_fee helper with off-by-one bug       -> Unit
#   #4 await asyncio.sleep(0.5) in list_cards               -> Performance
#   #5 requirements.txt: pin requests==2.20.0 (CVEs)        -> Vulnerability (SCA)
#   #6 frontend index.html + app.js: Instant Limit Increase -> UI
#      promo banner with a button that calls the new endpoint
#
# Refuses to run on a dirty workspace. Use revert_to_baseline.ps1 between runs.
# ============================================================================

$RepoRoot   = Split-Path -Parent $PSScriptRoot
$CcsPath    = Join-Path $RepoRoot "backend\routers\credit_card_services.py"
$CcPath     = Join-Path $RepoRoot "backend\routers\credit_cards.py"
$ReqsPath   = Join-Path $RepoRoot "backend\requirements.txt"
$FeIndexPath= Join-Path $RepoRoot "frontend\index.html"
$FeAppPath  = Join-Path $RepoRoot "frontend\app.js"

Set-Location $RepoRoot
Write-Host "Working directory: $RepoRoot" -ForegroundColor Cyan

function Read-Utf8($path) {
    return [System.IO.File]::ReadAllText($path, [System.Text.UTF8Encoding]::new($false))
}

function Write-Utf8($path, $content) {
    [System.IO.File]::WriteAllText($path, $content, [System.Text.UTF8Encoding]::new($false))
}

function Fail($msg) {
    Write-Host "FAILED: $msg" -ForegroundColor Red
    exit 1
}

# Target-app paths this script mutates. Keep in sync with revert_to_baseline.ps1.
$TargetPaths = @("backend", "frontend")

# 0. Target-app paths must be clean. AQE work (anywhere under aqe/) is ALLOWED
# to be dirty -- it is preserved across demo iterations by design.
$dirtyTarget = git status --porcelain -- $TargetPaths 2>$null
if ($dirtyTarget) {
    Write-Host "Target paths (backend/, frontend/) are dirty:" -ForegroundColor Red
    Write-Host $dirtyTarget
    Write-Host ""
    Fail "Run demo_scripts\revert_to_baseline.ps1 first."
}

# 0b. Verify the target-app paths match the baseline tag. HEAD itself may be
# ahead of baseline (the revert script adds a 'revert target app' commit) --
# what matters is that backend/ and frontend/ on disk equal their state at the
# baseline tag.
$baselineSha = "$(git rev-parse aqe-demo-baseline 2>$null)".Trim()
if (-not $baselineSha) { Fail "aqe-demo-baseline tag not found. Run setup_demo_repo.ps1." }
$targetDiffFromBaseline = git diff aqe-demo-baseline -- $TargetPaths
if ($targetDiffFromBaseline) {
    Write-Host "Target paths differ from baseline -- revert before pushing the feature." -ForegroundColor Yellow
    Fail "Revert to baseline first."
}

Write-Host ""
Write-Host "Applying 6 mutations across 5 files..." -ForegroundColor Yellow

# ---- Mutation #1, #2, #3 - credit_card_services.py ------------------------
$ccs = Read-Utf8 $CcsPath
if (-not $ccs.Contains("from core.db import get_async_db")) {
    Fail "Anchor not found in credit_card_services.py (import block)."
}

if (-not ($ccs -match "(?m)^import os\b")) {
    $ccs = $ccs -replace "(?m)^import random\b", "import os`r`nimport random"
}

$helperBlock = @'

def _calculate_late_fee(days_overdue: int, daily_rate: float) -> float:
    """Compute a compounding late fee. The fee grows 1% per day overdue.

    NOTE: this function has a deliberate off-by-one error in the day counter.
    Day 1 should apply the base daily_rate (multiplier 1.00); day 2 multiplier 1.01; etc.
    """
    if days_overdue <= 0:
        return 0.0
    total = 0.0
    for d in range(days_overdue):  # BUG: should be range(1, days_overdue + 1)
        total += daily_rate * (1.0 + d * 0.01)
    return round(total, 2)

'@
$logAnchor = 'log = get_logger("CreditCardServices")'
if ($ccs.Contains($logAnchor) -and -not $ccs.Contains("_calculate_late_fee")) {
    $ccs = $ccs.Replace($logAnchor, $logAnchor + "`r`n" + $helperBlock)
} elseif (-not $ccs.Contains("_calculate_late_fee")) {
    Fail "Anchor not found in credit_card_services.py (log = get_logger)."
}

$newEndpoint = @'

# ---- feat: credit limit increase (added by demo push) ---------------------
class LimitIncreaseRequest(BaseModel):
    delta_amount: float
    reason: str = ""


@router.post("/{card_id}/limit-increase")
async def increase_credit_limit(card_id: str, body: LimitIncreaseRequest) -> dict:
    """Increase a card's credit limit. (no auth check, accepts negative deltas)"""
    db = get_async_db()
    try:
        oid = ObjectId(card_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=422, detail="invalid card_id")
    card = await db.credit_cards.find_one({"_id": oid})
    if not card:
        raise HTTPException(status_code=404, detail="card not found")
    current = float(str(card.get("credit_limit") or 0))
    new_limit = current + float(body.delta_amount)
    await db.credit_cards.update_one({"_id": oid}, {"$set": {"credit_limit": new_limit}})
    log.info("credit_card.limit_increased", context={
        "card_id": card_id, "previous": current, "new": new_limit, "delta": body.delta_amount,
    })
    return {"card_id": card_id, "previous_limit": current, "new_limit": new_limit, "delta": body.delta_amount}
'@
if (-not $ccs.Contains("/limit-increase")) {
    $ccs = $ccs.TrimEnd() + "`r`n" + $newEndpoint + "`r`n"
}

$dispatchPattern = 'async def file_dispute'
if ($ccs -match $dispatchPattern) {
    $injected = "os.system(f'echo dispute filed for {card_id}: {body.reason} >> /tmp/disputes.log')"
    if (-not $ccs.Contains("os.system(f'echo dispute filed")) {
        $idx = $ccs.IndexOf($dispatchPattern)
        if ($idx -ge 0) {
            $dbIdx = $ccs.IndexOf("db = get_async_db()", $idx)
            if ($dbIdx -ge 0) {
                $insertAt = $ccs.IndexOf("`n", $dbIdx) + 1
                $lineStart = $ccs.LastIndexOf("`n", $dbIdx) + 1
                $indent = ""
                for ($i = $lineStart; $i -lt $dbIdx; $i++) {
                    $ch = $ccs[$i]
                    if ($ch -eq ' ' -or $ch -eq "`t") { $indent += $ch } else { break }
                }
                $injectLine = "$indent$injected`r`n"
                $ccs = $ccs.Insert($insertAt, $injectLine)
            }
        }
    }
} else {
    Write-Host "  (skipped #2 - file_dispute handler not found)" -ForegroundColor DarkGray
}

Write-Utf8 $CcsPath $ccs
Write-Host "  #1 + #2 + #3 applied to backend/routers/credit_card_services.py" -ForegroundColor Green

# ---- Mutation #4 - credit_cards.py perf regression ------------------------
$cc = Read-Utf8 $CcPath
if (-not ($cc -match "(?m)^import asyncio\b")) {
    $cc = $cc -replace "(?m)^from datetime import datetime, timezone\b", "import asyncio`r`nfrom datetime import datetime, timezone"
}
$listAnchor = "async def list_cards("
if ($cc.Contains($listAnchor)) {
    if (-not $cc.Contains("await asyncio.sleep(0.5)  # demo perf regression")) {
        $startIdx = $cc.IndexOf($listAnchor)
        $dbIdx = $cc.IndexOf("db = get_async_db()", $startIdx)
        if ($dbIdx -ge 0) {
            $insertAt = $cc.IndexOf("`n", $dbIdx) + 1
            $indent = "    "
            $cc = $cc.Insert($insertAt, "${indent}await asyncio.sleep(0.5)  # demo perf regression`r`n")
        }
    }
} else {
    Write-Host "  (skipped #4 - list_cards anchor not found)" -ForegroundColor DarkGray
}
Write-Utf8 $CcPath $cc
Write-Host "  #4 applied to backend/routers/credit_cards.py" -ForegroundColor Green

# ---- Mutation #5 - requirements.txt CVE dependency ------------------------
# We previously pinned requests==2.20.0, but that version requires idna<2.8
# while the FastAPI stack's anyio requires idna>=2.8 -- pip can't resolve.
# requests==2.25.1 still ships with multiple CVEs flagged by pip-audit
# (CVE-2023-32681, CVE-2024-35195, CVE-2024-47081) so the AQE vulnerability
# scanner still has something interesting to find, and its idna constraint
# (<3,>=2.5) is compatible with anyio so the docker image builds cleanly.
$reqs = Read-Utf8 $ReqsPath
# Drop any prior (broken) pin so a stale workspace converges to the new version
$reqs = $reqs -replace "(?m)^# Added by demo push - known CVEs.*\r?\n", ""
$reqs = $reqs -replace "(?m)^# \(CVE-.*\r?\n", ""
$reqs = $reqs -replace "(?m)^# Pinned to a version.*\r?\n", ""
$reqs = $reqs -replace "(?m)^# is compatible.*\r?\n", ""
$reqs = $reqs -replace "(?m)^requests==2\.20\.0\r?\n", ""
$reqs = $reqs -replace "(?m)^requests==2\.25\.1\r?\n", ""
$reqs = $reqs.TrimEnd() + "`r`n# Added by demo push - known CVEs flagged by pip-audit`r`n# (CVE-2023-32681, CVE-2024-35195, CVE-2024-47081). idna<3 is compatible`r`n# with anyio's idna>=2.8 so the api image still builds.`r`nrequests==2.25.1`r`n"
Write-Utf8 $ReqsPath $reqs
Write-Host "  #5 applied to backend/requirements.txt" -ForegroundColor Green

# ---- Mutation #6 - frontend Instant Limit Increase banner + handler -------
$fe = Read-Utf8 $FeIndexPath
$feAnchor = '<h1 class="page-title">Card &amp; Account Services</h1>'
$feBanner = @'

        <!-- NEW: Instant Limit Increase promotional banner (added by demo push) -->
        <div id="ilim-banner" style="background:linear-gradient(135deg,#012169 0%,#1A3A8F 100%);color:#fff;padding:20px 24px;border-radius:8px;margin-bottom:24px;display:flex;align-items:center;gap:20px;">
          <div style="font-size:36px;">&#9889;</div>
          <div style="flex:1;">
            <div style="font-size:18px;font-weight:700;margin-bottom:4px;">Instant Credit Limit Increase</div>
            <div style="font-size:13px;opacity:.9;">Get an immediate decision on your new credit line. No credit check required for amounts under $5,000.</div>
          </div>
          <button id="btn-instant-limit-increase" onclick="instantLimitIncrease()" style="background:#fff;color:#012169;border:none;padding:11px 22px;border-radius:6px;font-weight:700;font-size:13px;cursor:pointer;">Request Now</button>
        </div>

'@
if (-not $fe.Contains('id="ilim-banner"') -and $fe.Contains($feAnchor)) {
    $fe = $fe.Replace($feAnchor, $feAnchor + "`r`n" + $feBanner)
    Write-Utf8 $FeIndexPath $fe
    Write-Host "  #6a applied to frontend/index.html" -ForegroundColor Green
} else {
    Write-Host "  (skipped #6a - banner already present or anchor not found)" -ForegroundColor DarkGray
}

$feApp = Read-Utf8 $FeAppPath
if (-not $feApp.Contains("function instantLimitIncrease")) {
    $handler = @'

// Added by demo push: instant limit-increase widget on services page
async function instantLimitIncrease() {
  var btn = document.getElementById('btn-instant-limit-increase');
  if (btn) { btn.disabled = true; btn.textContent = 'Processing...'; }
  try {
    var listResp = await fetch('/api/v1/credit-cards?status=ACTIVE&limit=1');
    var listData = await listResp.json();
    if (!listData.cards || !listData.cards.length) {
      alert('No active cards found.');
      if (btn) { btn.disabled = false; btn.textContent = 'Request Now'; }
      return;
    }
    var cardId = listData.cards[0]._id || listData.cards[0].id;
    var amount = parseFloat(prompt('Increase amount (USD):', '1000')) || 0;
    if (!amount) {
      if (btn) { btn.disabled = false; btn.textContent = 'Request Now'; }
      return;
    }
    var resp = await fetch('/api/v1/credit-cards/' + cardId + '/limit-increase', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ delta_amount: amount, reason: 'instant request from services page' })
    });
    var data = await resp.json();
    if (resp.ok) {
      alert('Approved. New limit: $' + (data.new_limit || '?'));
    } else {
      alert('Request failed: ' + (data.detail || resp.status));
    }
  } catch (e) {
    alert('Error: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Request Now'; }
  }
}
window.instantLimitIncrease = instantLimitIncrease;
'@
    $feApp = $feApp.TrimEnd() + "`r`n" + $handler + "`r`n"
    Write-Utf8 $FeAppPath $feApp
    Write-Host "  #6b applied to frontend/app.js" -ForegroundColor Green
} else {
    Write-Host "  (skipped #6b - handler already present)" -ForegroundColor DarkGray
}

# ---- Commit + push --------------------------------------------------------
Write-Host ""
Write-Host "Committing and pushing..." -ForegroundColor Yellow
git add backend/routers/credit_card_services.py backend/routers/credit_cards.py backend/requirements.txt frontend/index.html frontend/app.js
$diffStat = git diff --cached --shortstat
Write-Host "  $diffStat" -ForegroundColor DarkGray

git commit -m "feat: credit-limit-increase end-to-end (backend + frontend + ui)" | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "git commit failed" }

git push origin main
if ($LASTEXITCODE -ne 0) { Fail "git push origin main failed" }

# ---- Rebuild api image + restart so the target picks up backend code ------
# `docker compose restart` does NOT rebuild a baked image. The api service is
# built from ./backend (no bind mount), so a plain restart leaves the running
# container on the old image and every new endpoint 404s. We must `up --build`
# the api service. The ui service IS bind-mounted to ./frontend so a restart
# is enough there.
Write-Host ""
Write-Host "Rebuilding api image + restarting services..." -ForegroundColor Yellow
docker compose up -d --build api
if ($LASTEXITCODE -ne 0) {
    Write-Host "  (docker compose up --build api failed - services may not be running)" -ForegroundColor DarkYellow
}
docker compose restart ui
if ($LASTEXITCODE -ne 0) {
    Write-Host "  (docker compose restart ui failed)" -ForegroundColor DarkYellow
}

# ---- Wait until the new endpoint is actually live -------------------------
# Without this we sometimes pushed to git, told the user "done", and triggered
# AQE before the rebuilt container finished booting -> every limit-increase
# test 404s and the demo story collapses.
Write-Host "Waiting for api to expose /limit-increase..." -ForegroundColor Yellow
$deadline = (Get-Date).AddSeconds(60)
$ready = $false
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:8000/openapi.json" -UseBasicParsing -TimeoutSec 3
        if ($r.Content.Contains("/limit-increase")) { $ready = $true; break }
    } catch {}
    Start-Sleep -Seconds 2
}
if (-not $ready) {
    Fail "api container did not expose /limit-increase within 60s - rebuild may have failed."
}
Write-Host "  api ready - /limit-increase endpoint is live." -ForegroundColor Green

# ---- Summary --------------------------------------------------------------
$newSha = "$(git rev-parse HEAD)".Trim()
Write-Host ""
Write-Host "Push complete." -ForegroundColor Green
Write-Host "  Baseline:    $($baselineSha.Substring(0,12))" -ForegroundColor Green
Write-Host "  New HEAD:    $($newSha.Substring(0,12))" -ForegroundColor Green
Write-Host "  Commit URL:  https://github.com/lakshmeesh12/boa/commit/$newSha" -ForegroundColor Green
Write-Host ""
Write-Host "Open AQE (http://localhost:5001) - the Changes banner should now show 5 files changed." -ForegroundColor Cyan
