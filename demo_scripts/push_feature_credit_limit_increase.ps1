# ============================================================================
# push_feature_credit_limit_increase.ps1
#
# Simulates a developer pushing a multi-file "feature" to the BOA target.
# Every mutation is designed to be picked up by exactly one AQE category so
# the resulting test run exercises the full pipeline:
#
#   #1 New endpoint POST /credit-cards/{id}/limit-increase  -> API + Functional
#   #2 import os + os.system in dispute handler             -> Vulnerability (SAST)
#   #3 _calculate_late_fee helper with off-by-one bug       -> Unit
#   #4 await asyncio.sleep(0.5) in list_cards               -> Performance
#   #5 requirements.txt: pin requests==2.20.0 (CVEs)        -> Vulnerability (SCA)
#
# After mutating, commits, pushes to GitHub, and restarts docker.
# Refuses to run on a dirty workspace.
#
# Revert with: demo_scripts\revert_to_baseline.ps1
# ============================================================================

$RepoRoot   = Split-Path -Parent $PSScriptRoot
$CcsPath    = Join-Path $RepoRoot "backend\routers\credit_card_services.py"
$CcPath     = Join-Path $RepoRoot "backend\routers\credit_cards.py"
$ReqsPath   = Join-Path $RepoRoot "backend\requirements.txt"

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

# 0. Workspace must be clean (force revert before re-pushing)
$dirty = git status --porcelain
if ($dirty) {
    Write-Host "Workspace is dirty:" -ForegroundColor Red
    Write-Host $dirty
    Write-Host ""
    Fail "Run demo_scripts\revert_to_baseline.ps1 first."
}

# 0b. Verify we are on the baseline (commit == aqe-demo-baseline)
$baselineSha = "$(git rev-parse aqe-demo-baseline 2>$null)".Trim()
$headSha     = "$(git rev-parse HEAD 2>$null)".Trim()
if (-not $baselineSha) { Fail "aqe-demo-baseline tag not found. Run setup_demo_repo.ps1." }
if ($headSha -ne $baselineSha) {
    Write-Host "HEAD ($($headSha.Substring(0,7))) is ahead of baseline ($($baselineSha.Substring(0,7)))" -ForegroundColor Yellow
    Fail "Revert to baseline first."
}

Write-Host ""
Write-Host "Applying 5 mutations across 3 files..." -ForegroundColor Yellow

# ---- Mutation #1, #2, #3 - credit_card_services.py ------------------------
$ccs = Read-Utf8 $CcsPath
if (-not $ccs.Contains("from core.db import get_async_db")) {
    Fail "Anchor not found in credit_card_services.py (import block)."
}

# Insert `import os` once, just after `import random` (Mutation #2 prep)
if (-not ($ccs -match "(?m)^import os\b")) {
    $ccs = $ccs -replace "(?m)^import random\b", "import os`r`nimport random"
}

# Inject helper function with off-by-one bug right after `log = get_logger(...)`
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

# Append new POST /limit-increase endpoint at the end of the file (Mutation #1)
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

# Mutation #2: inject os.system inside the dispute handler.
# Find the dispute function and add an os.system line just inside the function body.
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

# ---- Commit + push --------------------------------------------------------
Write-Host ""
Write-Host "Committing and pushing..." -ForegroundColor Yellow
git add backend/routers/credit_card_services.py backend/routers/credit_cards.py backend/requirements.txt
$diffStat = git diff --cached --shortstat
Write-Host "  $diffStat" -ForegroundColor DarkGray

git commit -m "feat: credit-limit-increase + late-fee helper (demo push)" | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "git commit failed" }

git push origin main
if ($LASTEXITCODE -ne 0) { Fail "git push origin main failed" }

# ---- Restart docker services so the target picks up code changes ----------
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
Write-Host "Open AQE (http://localhost:5001) - the Changes banner should now show 3 files changed." -ForegroundColor Cyan
