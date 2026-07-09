# update.ps1
# ПРИНУДИТЕЛЬНОЕ ОБНОВЛЕНИЕ GITHUB С ЗАМЕНОЙ

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host ""
Write-Host "========================================"
Write-Host "  🚀 FORCE UPDATE TO GITHUB & RENDER"
Write-Host "========================================"
Write-Host ""

# ========== ПЕРЕХОД В ПАПКУ ПРОЕКТА ==========
$projectPath = "E:\ДОКУМЕНТЫ\PROJECTS\my-trading-bot"
if (Test-Path $projectPath) {
    Write-Host "📁 Переход в папку проекта: $projectPath" -ForegroundColor Cyan
    Set-Location $projectPath
    Write-Host "✅ Текущая папка: $(Get-Location)" -ForegroundColor Green
} else {
    Write-Host "❌ Папка проекта не найдена: $projectPath" -ForegroundColor Red
    Write-Host ""
    Write-Host "💡 Попробуйте указать правильный путь:" -ForegroundColor Yellow
    Write-Host "   $projectPath" -ForegroundColor White
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host ""

# Проверка Git
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

# Проверка удалённого репозитория
$remote = git remote -v 2>$null
if (-not $remote) {
    Write-Host "📡 Добавляем удалённый репозиторий..." -ForegroundColor Cyan
    git remote add origin https://github.com/Valron2025/my-trading-bot.git
    Write-Host "✅ Удалённый репозиторий добавлен" -ForegroundColor Green
    Write-Host ""
}

# ========== ПРОВЕРКА КОНФЛИКТОВ ==========
Write-Host "🔄 Проверка конфликтов с удалённым репозиторием..." -ForegroundColor Cyan
git fetch origin 2>$null

if ($LASTEXITCODE -eq 0) {
    $localCommit = git rev-parse HEAD 2>$null
    $remoteCommit = git rev-parse origin/$branch 2>$null

    if ($localCommit -ne $remoteCommit -and $remoteCommit) {
        Write-Host "⚠️ Обнаружены изменения на GitHub!" -ForegroundColor Yellow
        Write-Host "   Локальный: $localCommit" -ForegroundColor Gray
        Write-Host "   Удалённый: $remoteCommit" -ForegroundColor Gray
        Write-Host ""
        Write-Host "💡 Будет выполнен принудительный push (force push)" -ForegroundColor Yellow
        Write-Host ""

        $confirm = Read-Host "Продолжить с force push? (y/n)"
        if ($confirm -ne "y") {
            Write-Host "❌ Отменено пользователем" -ForegroundColor Red
            Read-Host "Press Enter to exit"
            exit 0
        }
    }
}
Write-Host ""

# ========== ДОБАВЛЯЕМ ВСЕ ФАЙЛЫ ==========
Write-Host "📦 Добавляем все файлы..." -ForegroundColor Cyan
git add -A
Write-Host "✅ Все файлы добавлены" -ForegroundColor Green
Write-Host ""

# Показываем изменения
Write-Host "📝 Изменения для отправки:" -ForegroundColor Yellow
git status --short
Write-Host ""

# ========== СОЗДАЁМ КОММИТ ==========
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$commitMsg = "force-update $timestamp"

Write-Host "📝 Создаём коммит: $commitMsg" -ForegroundColor Cyan
git commit -m "$commitMsg" --allow-empty

if ($LASTEXITCODE -ne 0) {
    Write-Host "⚠️ Создаём пустой коммит..." -ForegroundColor Yellow
    git commit --allow-empty -m "empty-commit $timestamp"
}
Write-Host "✅ Коммит создан" -ForegroundColor Green
Write-Host ""

# ========== ПРИНУДИТЕЛЬНЫЙ PUSH (СНАЧАЛА ПРОВЕРЯЕМ) ==========
Write-Host "🚀 Отправка на GitHub..." -ForegroundColor Cyan

# Сначала пробуем обычный push
git push origin $branch

if ($LASTEXITCODE -ne 0) {
    Write-Host "⚠️ Обычный push не удался, пробуем force-with-lease..." -ForegroundColor Yellow
    git push origin $branch --force-with-lease

    if ($LASTEXITCODE -ne 0) {
        Write-Host "⚠️ Force-with-lease не удался, пробуем force..." -ForegroundColor Yellow

        # Предупреждение перед force push
        Write-Host ""
        Write-Host "⚠️ ВНИМАНИЕ! Force push ПЕРЕЗАПИШЕТ историю на GitHub!" -ForegroundColor Red
        Write-Host "   Это может удалить чужие коммиты (если они есть)" -ForegroundColor Yellow
        Write-Host ""

        $confirm = Read-Host "Вы уверены? (введите 'yes' для подтверждения)"
        if ($confirm -eq "yes") {
            git push origin $branch --force

            if ($LASTEXITCODE -eq 0) {
                Write-Host "✅ Force push успешен!" -ForegroundColor Green
            } else {
                Write-Host "❌ Force push не удался!" -ForegroundColor Red
                Write-Host ""
                Write-Host "Попробуйте выполнить вручную:" -ForegroundColor Yellow
                Write-Host "   git pull origin $branch --rebase" -ForegroundColor White
                Write-Host "   git push origin $branch" -ForegroundColor White
                Read-Host "Press Enter to exit"
                exit 1
            }
        } else {
            Write-Host "❌ Force push отменён" -ForegroundColor Red
            Write-Host ""
            Write-Host "Попробуйте выполнить вручную:" -ForegroundColor Yellow
            Write-Host "   git pull origin $branch --rebase" -ForegroundColor White
            Write-Host "   git push origin $branch" -ForegroundColor White
            Read-Host "Press Enter to exit"
            exit 0
        }
    } else {
        Write-Host "✅ Force-with-lease успешен!" -ForegroundColor Green
    }
} else {
    Write-Host "✅ Push успешен!" -ForegroundColor Green
}
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
Write-Host "💡 Render автоматически перезапустит бота" -ForegroundColor Yellow
Write-Host ""
Read-Host "Press Enter to exit"