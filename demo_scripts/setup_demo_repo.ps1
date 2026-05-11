# ============================================================================
# setup_demo_repo.ps1 - one-time baseline tag + initial push for AQE demo
#
# Pre-req: .gitignore must already exist at repo root.
# Idempotent - safe to run again; skips already-completed steps.
# ============================================================================

# Note: we do NOT use $ErrorActionPreference = "Stop" because PowerShell 5.1
# treats native-command stderr as terminating errors even with 2> redirection.
# Instead, check $LASTEXITCODE after each git call.

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Remote   = "https://github.com/lakshmeesh12/boa.git"
$Tag      = "aqe-demo-baseline"

Set-Location $RepoRoot
Write-Host "Working directory: $RepoRoot" -ForegroundColor Cyan

function Assert-GitOk {
    param([string]$What)
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $What (exit $LASTEXITCODE)" -ForegroundColor Red
        exit 1
    }
}

# 0. Sanity: .gitignore must exist
if (-not (Test-Path ".gitignore")) {
    Write-Host ".gitignore not found at repo root. Create it before running this script." -ForegroundColor Red
    exit 1
}

# 1. Initialize git if not already initialized
if (-not (Test-Path ".git")) {
    Write-Host "[1/6] git init" -ForegroundColor Yellow
    git init | Out-Null
    Assert-GitOk "git init"
} else {
    Write-Host "[1/6] git repo already initialized - skipping" -ForegroundColor DarkGray
}

# 2. Initial commit if no commits yet (must happen BEFORE branch rename)
git rev-parse --verify HEAD 2>&1 | Out-Null
$hasCommit = ($LASTEXITCODE -eq 0)

if (-not $hasCommit) {
    Write-Host "[2/6] staging files and creating baseline commit" -ForegroundColor Yellow
    git add .
    Assert-GitOk "git add"
    git commit -m "baseline: BOA + AQE pre-demo state" | Out-Null
    Assert-GitOk "git commit"
} else {
    Write-Host "[2/6] commit already exists - skipping" -ForegroundColor DarkGray
}

# 3. Ensure branch is main (now safe - we have at least one commit)
$branchOutput = git symbolic-ref --short HEAD 2>$null
if ($LASTEXITCODE -eq 0 -and $branchOutput) {
    $currentBranch = "$branchOutput".Trim()
} else {
    $currentBranch = ""
}

if ($currentBranch -ne "main") {
    Write-Host "[3/6] renaming branch '$currentBranch' to main" -ForegroundColor Yellow
    git branch -M main
    Assert-GitOk "git branch -M main"
} else {
    Write-Host "[3/6] already on main" -ForegroundColor DarkGray
}

# 4. Configure remote (or update existing one)
$remoteOutput = git remote get-url origin 2>$null
$remoteOk = ($LASTEXITCODE -eq 0)
if ($remoteOk -and $remoteOutput) {
    $existingRemote = "$remoteOutput".Trim()
} else {
    $existingRemote = ""
}

if (-not $remoteOk) {
    Write-Host "[4/6] adding origin remote: $Remote" -ForegroundColor Yellow
    git remote add origin $Remote
    Assert-GitOk "git remote add origin"
} elseif ($existingRemote -ne $Remote) {
    Write-Host "[4/6] updating origin remote URL: $Remote" -ForegroundColor Yellow
    git remote set-url origin $Remote
    Assert-GitOk "git remote set-url"
} else {
    Write-Host "[4/6] origin remote already set" -ForegroundColor DarkGray
}

# 5. Push main
Write-Host "[5/6] pushing main to origin" -ForegroundColor Yellow
git push -u origin main
Assert-GitOk "git push -u origin main"

# 6. Create and push baseline tag
$tagOutput = git tag -l $Tag
if ($tagOutput) {
    $existingTag = "$tagOutput".Trim()
} else {
    $existingTag = ""
}

if (-not $existingTag) {
    Write-Host "[6/6] creating tag $Tag and pushing" -ForegroundColor Yellow
    git tag $Tag
    Assert-GitOk "git tag"
    git push origin $Tag
    Assert-GitOk "git push origin tag"
} else {
    Write-Host "[6/6] tag $Tag already exists - skipping" -ForegroundColor DarkGray
}

$baselineSha = "$(git rev-parse $Tag)".Trim()
Write-Host ""
Write-Host "Baseline ready." -ForegroundColor Green
Write-Host "  Tag:    $Tag" -ForegroundColor Green
Write-Host "  SHA:    $baselineSha" -ForegroundColor Green
Write-Host "  Remote: $Remote" -ForegroundColor Green
