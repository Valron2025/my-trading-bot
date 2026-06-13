# update.ps1
# SENDING CHANGES TO GITHUB

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "========================================"
Write-Host "  UPDATE TO GITHUB"
Write-Host "========================================"
Write-Host ""

# ========== AUTO-DETECT PROJECT FOLDER ==========
$projectPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectPath
Write-Host "Project folder: $projectPath" -ForegroundColor Cyan
Write-Host ""

# ========== CHECK GIT ==========
try {
    $gitVersion = git --version 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Git not found" }
    Write-Host "OK: $gitVersion" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Git not found!" -ForegroundColor Red
    Write-Host "Please install Git: https://git-scm.com/download/win" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host ""

# ========== CHECK GIT CONFIG ==========
$userName = git config --global user.name 2>$null
$userEmail = git config --global user.email 2>$null

if (-not $userName -or -not $userEmail) {
    Write-Host "WARNING: Git user not configured!" -ForegroundColor Yellow
    Write-Host "Configuring Git..." -ForegroundColor Cyan

    $defaultName = "Valron2025"
    $defaultEmail = "valron2025@github.com"

    git config --global user.name $defaultName
    git config --global user.email $defaultEmail

    Write-Host "OK: Git configured: $defaultName <$defaultEmail>" -ForegroundColor Green
    Write-Host ""
}

# ========== INIT REPOSITORY ==========
if (-not (Test-Path ".git")) {
    Write-Host "WARNING: Git repository not initialized!" -ForegroundColor Yellow
    git init
    Write-Host "OK: Repository initialized" -ForegroundColor Green

    $remoteUrl = git remote get-url origin 2>$null
    if (-not $remoteUrl) {
        git remote add origin https://github.com/Valron2025/my-trading-bot.git
        Write-Host "OK: Remote origin added" -ForegroundColor Green
    }
    Write-Host ""
}

# Current branch
$branch = git branch --show-current 2>$null
if (-not $branch -or $branch -eq "") {
    $branch = "main"
    Write-Host "Creating branch $branch..." -ForegroundColor Cyan

    $branchExists = git show-ref --verify --quiet refs/heads/$branch
    if ($LASTEXITCODE -ne 0) {
        git checkout -b $branch
    } else {
        git checkout $branch
    }
} else {
    Write-Host "Current branch: $branch" -ForegroundColor Cyan
}
Write-Host ""

# ========== CHECK REMOTE ==========
$remoteUrl = git remote get-url origin 2>$null
if (-not $remoteUrl) {
    Write-Host "WARNING: Remote origin not configured!" -ForegroundColor Yellow
    git remote add origin https://github.com/Valron2025/my-trading-bot.git
    Write-Host "OK: Remote origin added" -ForegroundColor Green
    Write-Host ""
}

# ========== CREATE .gitignore ==========
$gitignorePath = ".gitignore"
if (-not (Test-Path $gitignorePath)) {
    Write-Host "Creating .gitignore..." -ForegroundColor Cyan
    @"
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
env/
venv/
.venv/
ENV/
env.bak/
venv.bak/

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# Logs
*.log
logs/
*.pid
*.seed
*.pid.lock

# Environment
.env
.venv
.envrc

# Trading data
*.db
*.sqlite
*.sqlite3
data/
cache/

# OS
.DS_Store
Thumbs.db
desktop.ini

# Secrets
*.key
*.pem
*.crt
secrets/
credentials.json

# Temporary files
tmp/
temp/
*.tmp
*.bak

# Testing
.pytest_cache/
.coverage
htmlcov/
.tox/
"@ | Out-File -FilePath $gitignorePath -Encoding UTF8
    Write-Host "OK: .gitignore created" -ForegroundColor Green
    Write-Host ""
}

# ========== ADD FILES ==========
Write-Host "Adding all files..." -ForegroundColor Cyan
git add -A

# Show changes
$changes = git status --short
if ($changes) {
    Write-Host ""
    Write-Host "Changes to commit:" -ForegroundColor Yellow
    Write-Host $changes
    Write-Host ""
} else {
    Write-Host "No changes to commit" -ForegroundColor Yellow
    Write-Host ""
}

# ========== CREATE COMMIT ==========
git diff --cached --quiet
$hasChangesExitCode = $LASTEXITCODE

if ($hasChangesExitCode -eq 0) {
    Write-Host "Nothing to commit" -ForegroundColor Green
} else {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $commitMsg = "Update from Windows - $timestamp"

    Write-Host "Creating commit: $commitMsg" -ForegroundColor Cyan
    $commitResult = git commit -m "$commitMsg" 2>&1

    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: Failed to create commit" -ForegroundColor Yellow
        Write-Host $commitResult -ForegroundColor Red
    } else {
        Write-Host "OK: Commit created" -ForegroundColor Green
    }
}
Write-Host ""

# ========== PULL LATEST CHANGES ==========
Write-Host "Pulling latest changes from GitHub..." -ForegroundColor Cyan
git pull origin $branch --no-rebase 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: Could not pull changes" -ForegroundColor Yellow
}
Write-Host ""

# ========== PUSH TO GITHUB ==========
Write-Host "Pushing to GitHub..." -ForegroundColor Cyan
$pushResult = git push origin $branch 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: Push failed, trying force for new branches..." -ForegroundColor Yellow

    $remoteBranchExists = git ls-remote --heads origin $branch 2>$null
    if (-not $remoteBranchExists) {
        Write-Host "New branch, using -u flag..." -ForegroundColor Cyan
        git push -u origin $branch
    } else {
        Write-Host "Branch exists but push failed" -ForegroundColor Yellow
        Write-Host "Possible solutions:" -ForegroundColor Cyan
        Write-Host "1. git pull origin $branch --rebase" -ForegroundColor Gray
        Write-Host "2. git push origin $branch --force (CAUTION!)" -ForegroundColor Gray
        Write-Host ""

        $answer = Read-Host "Do you want to force push? (y/N)"
        if ($answer -eq 'y' -or $answer -eq 'Y') {
            Write-Host "WARNING: Force pushing..." -ForegroundColor Red
            git push origin $branch --force
        }
    }
}

if ($LASTEXITCODE -eq 0) {
    Write-Host "OK: Push successful!" -ForegroundColor Green
} else {
    Write-Host "ERROR: Push failed!" -ForegroundColor Red
}
Write-Host ""

# ========== RESULT ==========
Write-Host "========================================"
Write-Host "SUCCESS!" -ForegroundColor Green
Write-Host "========================================"
Write-Host ""
Write-Host "Last commit:" -ForegroundColor Cyan
git log -1 --oneline
Write-Host ""
Write-Host "GitHub:" -ForegroundColor Cyan
Write-Host "   https://github.com/Valron2025/my-trading-bot" -ForegroundColor Blue
Write-Host ""
Write-Host "Render Dashboard:" -ForegroundColor Cyan
Write-Host "   https://dashboard.render.com" -ForegroundColor Blue
Write-Host ""

$currentTime = Get-Date -Format "HH:mm:ss"
Write-Host "[$currentTime] Done!" -ForegroundColor Green
Write-Host ""

Read-Host "Press Enter to exit"