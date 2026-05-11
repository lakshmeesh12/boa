# ============================================================================
# revert_to_baseline.ps1
#
# Hard-reset the workspace + remote back to the aqe-demo-baseline tag.
# Use this between demo iterations.
#
# Idempotent. Verifies HEAD == baseline before exiting.
# ============================================================================

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
Write-Host "Working directory: $RepoRoot" -ForegroundColor Cyan

$Tag = "aqe-demo-baseline"

# Resolve baseline SHA
$baselineSha = "$(git rev-parse $Tag 2>$null)".Trim()
if (-not $baselineSha) {
    Write-Host "FAILED: tag '$Tag' not found. Run setup_demo_repo.ps1 first." -ForegroundColor Red
    exit 1
}

Write-Host "Resetting workspace to $Tag ($($baselineSha.Substring(0,12)))..." -ForegroundColor Yellow
git reset --hard $Tag
if ($LASTEXITCODE -ne 0) { Write-Host "git reset failed" -ForegroundColor Red; exit 1 }

git clean -fd
if ($LASTEXITCODE -ne 0) { Write-Host "git clean failed" -ForegroundColor Red; exit 1 }

Write-Host "Force-pushing remote to baseline (safe: this is the demo repo only)..." -ForegroundColor Yellow
git push --force-with-lease origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "  (push failed — likely already at baseline, or no network)" -ForegroundColor DarkYellow
}

Write-Host "Restarting docker services..." -ForegroundColor Yellow
docker compose restart api ui
if ($LASTEXITCODE -ne 0) {
    Write-Host "  (docker restart failed — services may not be running)" -ForegroundColor DarkYellow
}

# Verify clean state
$dirty = git status --porcelain
if ($dirty) {
    Write-Host "REVERT INCOMPLETE: workspace still dirty:" -ForegroundColor Red
    Write-Host $dirty
    exit 1
}
$head = "$(git rev-parse HEAD)".Trim()
if ($head -ne $baselineSha) {
    Write-Host "REVERT INCOMPLETE: HEAD ($head) does not match baseline ($baselineSha)" -ForegroundColor Red
    exit 1
}

# Drop any cached change-analysis files so AQE re-analyzes next time
$cacheDir = Join-Path $RepoRoot "aqe\data\change_cache"
if (Test-Path $cacheDir) {
    Remove-Item -Path "$cacheDir\*" -Force -ErrorAction SilentlyContinue
    Write-Host "  cleared change_cache/" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "Reverted to baseline ($($baselineSha.Substring(0,12))). Workspace clean." -ForegroundColor Green
