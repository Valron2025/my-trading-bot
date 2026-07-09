# update.ps1 - FORCE UPDATE TO GITHUB
# Сохраните в UTF-8 кодировке!

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host ""
Write-Host "========================================"
Write-Host "  FORCE UPDATE TO GITHUB & RENDER"
Write-Host "========================================"
Write-Host ""

# ========== PROJECT PATH ==========
$projectPath = "E:\ДОКУМЕНТЫ\PROJECTS\my-trading-bot"
if (Test-Path $projectPath) {
    Write-Host "Project folder: $projectPath" -ForegroundColor Cyan
    Set-Location $projectPath
    Write-Host "Current folder: $(Get-Location)" -ForegroundColor Green
} else {
    Write-Host "Project folder not found: $projectPath" -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host ""

# Check Git
try {
    $gitVersion = git --version 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Git not found" }
    Write-Host "$gitVersion" -ForegroundColor Green
} catch {
    Write-Host "Git not found!" -ForegroundColor Red
    Write-Host "Install Git: https://git-scm.com/download/win" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host ""

# ========== INIT REPOSITORY ==========
if (-not (Test-Path ".git")) {
    Write-Host "Git repo not initialized!" -ForegroundColor Yellow
    git init
    git remote add origin https://github.com/Valron2025/my-trading-bot.git
    Write-Host "Repo initialized" -ForegroundColor Green
    Write-Host ""
}

# Current branch
$branch = git branch --show-current 2>$null
if (-not $branch) {
    $branch = "main"
    Write-Host "Creating branch $branch..." -ForegroundColor Cyan
    git checkout -b $branch
} else {
    Write-Host "Current branch: $branch" -ForegroundColor Cyan
}
Write-Host ""

# Check remote
$remote = git remote -v 2>$null
if (-not $remote) {
    Write-Host "Adding remote..." -ForegroundColor Cyan
    git remote add origin https://github.com/Valron2025/my-trading-bot.git
    Write-Host "Remote added" -ForegroundColor Green
    Write-Host ""
}

# ========== CHECK CONFLICTS ==========
Write-Host "Checking conflicts with remote..." -ForegroundColor Cyan
git fetch origin 2>$null

if ($LASTEXITCODE -eq 0) {
    $localCommit = git rev-parse HEAD 2>$null
    $remoteCommit = git rev-parse origin/$branch 2>$null

    if ($localCommit -ne $remoteCommit -and $remoteCommit) {
        Write-Host "Changes detected on GitHub!" -ForegroundColor Yellow
        Write-Host "   Local: $localCommit" -ForegroundColor Gray
        Write-Host "   Remote: $remoteCommit" -ForegroundColor Gray
        Write-Host ""
        Write-Host "Force push will be used" -ForegroundColor Yellow
        Write-Host ""

        $confirm = Read-Host "Continue with force push? (y/n)"
        if ($confirm -ne "y") {
            Write-Host "Cancelled by user" -ForegroundColor Red
            Read-Host "Press Enter to exit"
            exit 0
        }
    }
}
Write-Host ""

# ========== ADD ALL FILES ==========
Write-Host "Adding all files..." -ForegroundColor Cyan
git add -A
Write-Host "All files added" -ForegroundColor Green
Write-Host ""

# Show changes
Write-Host "Changes to push:" -ForegroundColor Yellow
git status --short
Write-Host ""

# ========== CREATE COMMIT ==========
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$commitMsg = "force-update $timestamp"

Write-Host "Creating commit: $commitMsg" -ForegroundColor Cyan
git commit -m "$commitMsg" --allow-empty

if ($LASTEXITCODE -ne 0) {
    Write-Host "Creating empty commit..." -ForegroundColor Yellow
    git commit --allow-empty -m "empty-commit $timestamp"
}
Write-Host "Commit created" -ForegroundColor Green
Write-Host ""

# ========== PUSH TO GITHUB ==========
Write-Host "Pushing to GitHub..." -ForegroundColor Cyan

# Try normal push first
git push origin $branch

if ($LASTEXITCODE -ne 0) {
    Write-Host "Normal push failed, trying force-with-lease..." -ForegroundColor Yellow
    git push origin $branch --force-with-lease

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Force-with-lease failed, trying force..." -ForegroundColor Yellow

        Write-Host ""
        Write-Host "WARNING! Force push will REWRITE history on GitHub!" -ForegroundColor Red
        Write-Host ""

        $confirm = Read-Host "Are you sure? (type 'yes' to confirm)"
        if ($confirm -eq "yes") {
            git push origin $branch --force

            if ($LASTEXITCODE -eq 0) {
                Write-Host "Force push successful!" -ForegroundColor Green
            } else {
                Write-Host "Force push failed!" -ForegroundColor Red
                Write-Host ""
                Write-Host "Try manually:" -ForegroundColor Yellow
                Write-Host "   git pull origin $branch --rebase" -ForegroundColor White
                Write-Host "   git push origin $branch" -ForegroundColor White
                Read-Host "Press Enter to exit"
                exit 1
            }
        } else {
            Write-Host "Force push cancelled" -ForegroundColor Red
            Write-Host ""
            Write-Host "Try manually:" -ForegroundColor Yellow
            Write-Host "   git pull origin $branch --rebase" -ForegroundColor White
            Write-Host "   git push origin $branch" -ForegroundColor White
            Read-Host "Press Enter to exit"
            exit 0
        }
    } else {
        Write-Host "Force-with-lease successful!" -ForegroundColor Green
    }
} else {
    Write-Host "Push successful!" -ForegroundColor Green
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
Write-Host "GitHub: https://github.com/Valron2025/my-trading-bot" -ForegroundColor Cyan
Write-Host "Render: https://dashboard.render.com" -ForegroundColor Cyan
Write-Host ""
Write-Host "Render will auto-redeploy the bot" -ForegroundColor Yellow
Write-Host ""
Read-Host "Press Enter to exit"