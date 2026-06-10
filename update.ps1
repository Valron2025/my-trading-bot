# update.ps1
# ПРИНУДИТЕЛЬНОЕ ОБНОВЛЕНИЕ GITHUB С ЗАМЕНОЙ И ОЧИСТКОЙ КЭША

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host ""
Write-Host "========================================"
Write-Host "  🚀 FORCE UPDATE TO GITHUB & RENDER"
Write-Host "========================================"
Write-Host ""

# ========== ПЕРЕХОД В ПАПКУ ПРОЕКТА ==========
$projectPath = "F:\PROJECTS\my-trading-bot"
if (Test-Path $projectPath) {
    Write-Host "📁 Переход в папку проекта: $projectPath" -ForegroundColor Cyan
    Set-Location $projectPath
    Write-Host "✅ Текущая папка: $(Get-Location)" -ForegroundColor Green
} else {
    Write-Host "❌ Папка проекта не найдена: $projectPath" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host ""

# ========== 🧹 ОЧИСТКА __pycache__ ПЕРЕД ОТПРАВКОЙ ==========
Write-Host "🧹 ОЧИСТКА КЭША Python (__pycache__)..." -ForegroundColor Cyan

# Удаляем все __pycache__ папки
$pycacheCount = 0
Get-ChildItem -Path . -Directory -Recurse -Filter "__pycache__" -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "   🗑️ Удаляем: $($_.FullName)" -ForegroundColor DarkGray
    Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    $pycacheCount++
}

# Удаляем все .pyc файлы
$pycCount = 0
Get-ChildItem -Path . -Recurse -Filter "*.pyc" -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "   🗑️ Удаляем: $($_.Name)" -ForegroundColor DarkGray
    Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue
    $pycCount++
}

Write-Host "✅ Очищено папок __pycache__: $pycacheCount" -ForegroundColor Green
Write-Host "✅ Очищено .pyc файлов: $pycCount" -ForegroundColor Green
Write-Host ""

# ========== 🧹 ОЧИСТКА ДРУГИХ ВРЕМЕННЫХ ФАЙЛОВ ==========
Write-Host "🧹 ОЧИСТКА ВРЕМЕННЫХ ФАЙЛОВ..." -ForegroundColor Cyan

$tempPatterns = @(
    "*.log", "*.tmp", "*.temp", "*.bak", "*.backup",
    "*.pid", "*.lock", ".DS_Store", "Thumbs.db",
    "*.egg-info", "build/", "dist/", "*.spec"
)

$tempCount = 0
foreach ($pattern in $tempPatterns) {
    Get-ChildItem -Path . -Recurse -Filter $pattern -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "   🗑️ Удаляем: $($_.FullName)" -ForegroundColor DarkGray
        Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue
        $tempCount++
    }
}

Write-Host "✅ Очищено временных файлов: $tempCount" -ForegroundColor Green
Write-Host ""

# ========== СОЗДАЁМ .gitignore ЕСЛИ НЕТ ==========
$gitignorePath = ".gitignore"
if (-not (Test-Path $gitignorePath)) {
    Write-Host "📝 Создаём .gitignore..." -ForegroundColor Cyan
    $gitignoreContent = @"
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
env/
venv/
ENV/
env.bak/
venv.bak/

# Logs
*.log
*.tmp
*.temp
*.bak
*.backup

# IDE
.vscode/
.idea/
*.swp
*.swo
*~
.DS_Store
Thumbs.db

# Build
build/
dist/
*.egg-info/
*.egg

# Secrets
*.env
*.secret
config_local.py
"@
    $gitignoreContent | Out-File -FilePath $gitignorePath -Encoding UTF8
    Write-Host "✅ .gitignore создан" -ForegroundColor Green
    Write-Host ""
} else {
    Write-Host "✅ .gitignore уже существует" -ForegroundColor Green
    Write-Host ""
}

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

# Проверка удалённого репозитория
$remote = git remote -v 2>$null
if (-not $remote) {
    Write-Host "📡 Добавляем удалённый репозиторий..." -ForegroundColor Cyan
    git remote add origin https://github.com/Valron2025/my-trading-bot.git
    Write-Host "✅ Удалённый репозиторий добавлен" -ForegroundColor Green
    Write-Host ""
}

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

# ========== ПРИНУДИТЕЛЬНЫЙ PUSH (ПОЛНАЯ ЗАМЕНА) ==========
Write-Host "🚀 Принудительная отправка на GitHub (force push)..." -ForegroundColor Cyan

# Пытаемся force push
git push origin $branch --force

if ($LASTEXITCODE -ne 0) {
    Write-Host "⚠️ Force push не удался, пробуем force-with-lease..." -ForegroundColor Yellow
    git push origin $branch --force-with-lease

    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Push не удался!" -ForegroundColor Red
        Write-Host ""
        Write-Host "Попробуйте выполнить вручную:" -ForegroundColor Yellow
        Write-Host "   cd $projectPath" -ForegroundColor White
        Write-Host "   git add -A" -ForegroundColor White
        Write-Host "   git commit -m 'force-update' --allow-empty" -ForegroundColor White
        Write-Host "   git push origin $branch --force" -ForegroundColor White
        Read-Host "Press Enter to exit"
        exit 1
    }
}

Write-Host "✅ Push успешен!" -ForegroundColor Green
Write-Host ""

# ========== ДОПОЛНИТЕЛЬНО: ОЧИСТКА ЛОКАЛЬНОГО КЭША GIT ==========
Write-Host "🧹 ОЧИСТКА ЛОКАЛЬНОГО КЭША GIT..." -ForegroundColor Cyan
git gc --auto 2>$null
Write-Host "✅ Локальный кэш Git очищен" -ForegroundColor Green
Write-Host ""

# ========== РЕЗУЛЬТАТ ==========
Write-Host "========================================"
Write-Host "✅ SUCCESS!" -ForegroundColor Green
Write-Host "========================================"
Write-Host ""
Write-Host "📊 Статистика очистки:" -ForegroundColor Cyan
Write-Host "   🗑️ __pycache__ папок: $pycacheCount" -ForegroundColor DarkGray
Write-Host "   🗑️ .pyc файлов: $pycCount" -ForegroundColor DarkGray
Write-Host "   🗑️ Временных файлов: $tempCount" -ForegroundColor DarkGray
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