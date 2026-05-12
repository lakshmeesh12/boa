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

# 0. Workspace must be clean
$dirty = git status --porcelain
if ($dirty) {
    Write-Host "Workspace is dirty:" -ForegroundColor Red
    Write-Host $dirty
    Write-Host ""
    Fail "Run demo_scripts\revert_to_baseline.ps1 first."
}

# 0b. Verify we are on the baseline
$baselineSha = "$(git rev-parse aqe-demo-baseline 2>$null)".Trim()
$headSha     = "$(git rev-parse HEAD 2>$null)".Trim()
if (-not $baselineSha) { Fail "aqe-demo-baseline tag not found. Run setup_demo_repo.ps1." }
if ($headSha -ne $baselineSha) {
    Write-Host "HEAD ($($headSha.Substring(0,7))) is ahead of baseline ($($baselineSha.Substring(0,7)))" -ForegroundColor Yellow
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
    current = float(card.get("credit_limit", 0) or 0)
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
$reqs = Read-Utf8 $ReqsPath
if (-not ($reqs -match "(?m)^requests==2\.20\.0\b")) {
    $reqs = $reqs.TrimEnd() + "`r`n# Added by demo push - known CVEs (CVE-2018-18074 et al.)`r`nrequests==2.20.0`r`n"
}
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

# ---- Restart docker so the target picks up code changes -------------------
Write-Host ""
Write-Host "Restarting docker services..." -ForegroundColor Yellow
docker compose restart api ui
if ($LASTEXITCODE -ne 0) {
    Write-Host "  (docker restart failed - services may not be running)" -ForegroundColor DarkYellow
}

# ---- Summary --------------------------------------------------------------
$newSha = "$(git rev-parse HEAD)".Trim()
Write-Host ""
Write-Host "Push complete." -ForegroundColor Green
Write-Host "  Baseline:    $($baselineSha.Substring(0,12))" -ForegroundColor Green
Write-Host "  New HEAD:    $($newSha.Substring(0,12))" -ForegroundColor Green
Write-Host "  Commit URL:  https://github.com/lakshmeesh12/boa/commit/$newSha" -ForegroundColor Green
Write-Host ""
Write-Host "Open AQE (http://localhost:5001) - the Changes banner should now show 5 files changed." -ForegroundColor Cyan
