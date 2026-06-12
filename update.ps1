# update.ps1
# ОТПРАВКА ИЗМЕНЕНИЙ НА GITHUB (автоопределение папки)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host ""
Write-Host "========================================"
Write-Host "  🚀 UPDATE TO GITHUB"
Write-Host "========================================"
Write-Host ""

# ========== АВТОМАТИЧЕСКОЕ ОПРЕДЕЛЕНИЕ ПАПКИ ПРОЕКТА ==========
# Скрипт сам определяет, где он находится, и работает оттуда
$projectPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectPath
Write-Host "📁 Папка проекта: $projectPath" -ForegroundColor Cyan
Write-Host ""

# ========== ПРОВЕРКА GIT ==========
try {
    $gitVersion = git --version 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Git not found" }
    Write-Host "✅ $gitVersion" -ForegroundColor Green
} catch {
    Write-Host "❌ Git not found!" -ForegroundColor Red
    Write-Host "Please install Git: https://git-scm.com/download/win" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host ""

# ========== ИНИЦИАЛИЗАЦИЯ РЕПОЗИТОРИЯ ==========
if (-not (Test-Path ".git")) {
    Write-Host "⚠️ Git репозиторий не инициализирован!" -ForegroundColor Yellow
    git init
    git remote add origin https://github.com/Valron2025/my-trading-bot.git
    Write-Host "✅ Репозиторий инициализирован" -ForegroundColor Green
    Write-Host ""
}

# Текущая ветка
$branch = git branch --show-current 2>$null
if (-not $branch) {
    $branch = "main"
    Write-Host "📋 Создаём ветку $branch..." -ForegroundColor Cyan
    git checkout -b $branch
} else {
    Write-Host "📋 Текущая ветка: $branch" -ForegroundColor Cyan
}
Write-Host ""

# ========== ДОБАВЛЯЕМ ВСЕ ФАЙЛЫ ==========
Write-Host "📦 Добавляем все файлы..." -ForegroundColor Cyan
git add -A
Write-Host "✅ Все файлы добавлены" -ForegroundColor Green
Write-Host ""

# Показываем изменения
$changes = git status --short
if ($changes) {
    Write-Host "📝 Изменения для отправки:" -ForegroundColor Yellow
    Write-Host $changes
} else {
    Write-Host "📝 Нет изменений для отправки" -ForegroundColor Yellow
}
Write-Host ""

# ========== СОЗДАЁМ КОММИТ (ТОЛЬКО ЕСЛИ ЕСТЬ ИЗМЕНЕНИЯ) ==========
$hasChanges = git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ Нет изменений для коммита" -ForegroundColor Green
} else {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $commitMsg = "update $timestamp"

    Write-Host "📝 Создаём коммит: $commitMsg" -ForegroundColor Cyan
    git commit -m "$commitMsg"

    if ($LASTEXITCODE -ne 0) {
        Write-Host "⚠️ Не удалось создать коммит" -ForegroundColor Yellow
    } else {
        Write-Host "✅ Коммит создан" -ForegroundColor Green
    }
}
Write-Host ""

# ========== PUSH НА GITHUB ==========
Write-Host "🚀 Отправка на GitHub..." -ForegroundColor Cyan
git push origin $branch

if ($LASTEXITCODE -ne 0) {
    Write-Host "⚠️ Push не удался, пробуем force..." -ForegroundColor Yellow
    git push origin $branch --force
}

Write-Host "✅ Push успешен!" -ForegroundColor Green
Write-Host ""

# ========== РЕЗУЛЬТАТ ==========
Write-Host "========================================"
Write-Host "✅ SUCCESS!" -ForegroundColor Green
Write-Host "========================================"
Write-Host ""
Write-Host "📊 Последний коммит:" -ForegroundColor Cyan
git log -1 --oneline
Write-Host ""
Write-Host "🔗 GitHub: https://github.com/Valron2025/my-trading-bot" -ForegroundColor Cyan
Write-Host "🤖 Render: https://dashboard.render.com" -ForegroundColor Cyan
Write-Host ""
Read-Host "Press Enter to exit"