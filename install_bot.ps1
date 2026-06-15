# install_bot.ps1 - Полная установка торгового бота
# НЕ ТРЕБУЕТ АДМИНИСТРАТОРА (если установка в пользовательскую папку)

param(
    [string]$InstallPath = "$env:USERPROFILE\my-trading-bot"
)

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   🤖 TRADING BOT INSTALLER" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "📁 Installation path: $InstallPath" -ForegroundColor DarkGray
Write-Host ""

# НЕ проверяем админа - просто работаем

# Create folder
Write-Host "📁 Creating installation folder..." -ForegroundColor Yellow
if (-not (Test-Path $InstallPath)) {
    New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
    Write-Host "   ✅ Folder created: $InstallPath" -ForegroundColor Green
} else {
    Write-Host "   ✅ Folder exists: $InstallPath" -ForegroundColor Green
}
Set-Location $InstallPath

# Check Python
Write-Host ""
Write-Host "🐍 Checking Python..." -ForegroundColor Yellow
try {
    $pyVer = python --version 2>&1
    Write-Host "   ✅ $pyVer" -ForegroundColor Green
} catch {
    Write-Host "   ❌ Python not found!" -ForegroundColor Red
    Write-Host "   Please install Python 3.11 from https://www.python.org/downloads/" -ForegroundColor Yellow
    pause
    exit 1
}

# Create virtual environment
Write-Host ""
Write-Host "📦 Creating virtual environment..." -ForegroundColor Yellow
if (-not (Test-Path "venv")) {
    python -m venv venv
    Write-Host "   ✅ Virtual environment created" -ForegroundColor Green
} else {
    Write-Host "   ✅ Virtual environment exists" -ForegroundColor Green
}

# Activate venv and install dependencies
Write-Host ""
Write-Host "📚 Installing dependencies..." -ForegroundColor Yellow
$venvPython = "$InstallPath\venv\Scripts\python.exe"
$venvPip = "$InstallPath\venv\Scripts\pip.exe"

# Create requirements.txt (полная версия)
@"
Flask>=2.3.3
python-dotenv>=1.0.0
requests>=2.31.0
grpcio>=1.60.0
certifi>=2024.0.0
t-tech-investments>=0.3.3
websockets>=12.0
nest-asyncio>=1.6.0
python-telegram-bot>=20.0
prometheus-client>=0.19.0
yfinance>=0.2.28
beautifulsoup4>=4.12.0
lxml>=4.9.0
numpy>=1.26.4
pandas>=2.1.4
"@ | Out-File -FilePath "$InstallPath\requirements.txt" -Encoding UTF8

Write-Host "   Upgrading pip..." -ForegroundColor DarkGray
& $venvPython -m pip install --upgrade pip --quiet

Write-Host "   Installing packages..." -ForegroundColor DarkGray
& $venvPip install -r requirements.txt --quiet

Write-Host "   ✅ Dependencies installed" -ForegroundColor Green

# Create .env template
Write-Host ""
Write-Host "🔐 Creating .env file..." -ForegroundColor Yellow
if (-not (Test-Path ".env")) {
    @"
# Trading Bot Configuration
TBANK_TOKEN=YOUR_TOKEN_HERE
TBANK_ACCOUNT_ID=
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=
LOG_LEVEL=INFO
"@ | Out-File -FilePath ".env" -Encoding UTF8
    Write-Host "   ⚠️ .env template created - ADD YOUR TOKENS!" -ForegroundColor Yellow
} else {
    Write-Host "   ✅ .env file exists" -ForegroundColor Green
}

# Create logs folder
Write-Host ""
Write-Host "📋 Creating logs folder..." -ForegroundColor Yellow
if (-not (Test-Path "logs")) {
    New-Item -ItemType Directory -Path "logs" -Force | Out-Null
    Write-Host "   ✅ Logs folder created" -ForegroundColor Green
} else {
    Write-Host "   ✅ Logs folder exists" -ForegroundColor Green
}

# Создаём все остальные скрипты (они будут без проверки админа)
Write-Host ""
Write-Host "🚀 Creating scripts..." -ForegroundColor Yellow

# start_bot.ps1 (без админа)
@'
# start_bot.ps1 - Запуск торгового бота
# НЕ ТРЕБУЕТ АДМИНИСТРАТОРА

param(
    [switch]$Background = $false
)

$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptPath

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  🚀 STARTING TRADING BOT" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "📅 Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray
Write-Host ""

# Check .env
if (-not (Test-Path ".env")) {
    Write-Host "❌ ERROR: .env file not found!" -ForegroundColor Red
    pause
    exit 1
}

# Check venv
if (-not (Test-Path "venv\Scripts\python.exe")) {
    Write-Host "❌ ERROR: Virtual environment not found!" -ForegroundColor Red
    Write-Host "   Run: .\install_bot.ps1 first" -ForegroundColor Yellow
    pause
    exit 1
}

if ($Background) {
    Write-Host "📡 Starting in BACKGROUND mode..." -ForegroundColor Cyan
    $process = Start-Process -FilePath "venv\Scripts\python.exe" -ArgumentList "web_server.py" -WindowStyle Hidden -PassThru
    Write-Host ""
    Write-Host "✅ BOT STARTED IN BACKGROUND!" -ForegroundColor Green
    Write-Host "   PID: $($process.Id)" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "📊 Check status: .\status.ps1" -ForegroundColor Cyan
    Write-Host "🛑 Stop bot: .\stop_bot.ps1" -ForegroundColor Cyan
    Write-Host "📋 View logs: .\logs.ps1" -ForegroundColor Cyan
} else {
    Write-Host "📡 Starting in CONSOLE mode..." -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
    Write-Host ""
    & "venv\Scripts\python.exe" web_server.py
}
'@ | Out-File -FilePath "$InstallPath\start_bot.ps1" -Encoding UTF8

# stop_bot.ps1 (без админа - не использует ScheduledTask)
@'
# stop_bot.ps1 - Остановка торгового бота
# НЕ ТРЕБУЕТ АДМИНИСТРАТОРА

$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptPath

Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  🛑 STOPPING TRADING BOT" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""
Write-Host "📅 Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray
Write-Host ""

# Stop Python processes from our folder
Write-Host "🐍 Stopping Python processes..." -ForegroundColor Cyan
$stopped = 0
$processes = Get-Process python -ErrorAction SilentlyContinue

foreach ($proc in $processes) {
    try {
        $procPath = $proc.Path
        if ($procPath -like "*my-trading-bot*" -or $procPath -like "*$scriptPath*") {
            Write-Host "   Stopping PID $($proc.Id)" -ForegroundColor DarkGray
            $proc.Kill()
            $stopped++
        }
    } catch {}
}
Write-Host "   ✅ Stopped $stopped processes" -ForegroundColor Green

# Free port 10000
Write-Host "🔌 Freeing port 10000..." -ForegroundColor Cyan
$connections = netstat -ano 2>$null | Select-String ":10000" | Select-String "LISTENING"
if ($connections) {
    foreach ($line in $connections) {
        $parts = $line -split '\s+'
        $pid = $parts[-1]
        if ($pid -match '^\d+$') {
            try {
                Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
                Write-Host "   ✅ Killed process on port 10000 (PID: $pid)" -ForegroundColor Green
            } catch {}
        }
    }
}

# Remove lock files
Remove-Item "$scriptPath\trading_bot.lock" -Force -ErrorAction SilentlyContinue
Remove-Item "$env:TEMP\trading_bot.lock" -Force -ErrorAction SilentlyContinue

Start-Sleep -Seconds 1

Write-Host ""
Write-Host "✅ BOT STOPPED" -ForegroundColor Green
Write-Host ""
Read-Host "Press Enter to exit"
'@ | Out-File -FilePath "$InstallPath\stop_bot.ps1" -Encoding UTF8

# status.ps1 (без админа)
@'
# status.ps1 - Проверка статуса бота

$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptPath

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  📊 BOT STATUS" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "📅 Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray
Write-Host ""

# Check processes
Write-Host "🐍 PYTHON PROCESSES:" -ForegroundColor Yellow
$processes = Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*my-trading-bot*" -or $_.Path -like "*$scriptPath*" }
if ($processes) {
    Write-Host "   ✅ Found $($processes.Count) process(es)" -ForegroundColor Green
    foreach ($proc in $processes) {
        $mem = [math]::Round($proc.WorkingSet64 / 1MB, 2)
        Write-Host "      PID: $($proc.Id) | Memory: ${mem} MB" -ForegroundColor DarkGray
    }
} else {
    Write-Host "   ❌ No Python processes found" -ForegroundColor Red
}
Write-Host ""

# Check port
Write-Host "🔌 PORT 10000:" -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://localhost:10000/health" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
    Write-Host "   ✅ LISTENING" -ForegroundColor Green
    Write-Host "   📡 Web server: OK" -ForegroundColor Green
} catch {
    Write-Host "   ❌ CLOSED (bot not running)" -ForegroundColor Red
}
Write-Host ""

# Check logs
$logFile = "logs\web_server.log"
if (Test-Path $logFile) {
    Write-Host "📋 LAST LOGS (5 lines):" -ForegroundColor Yellow
    Get-Content $logFile -Tail 5 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }
} else {
    Write-Host "📋 No logs yet" -ForegroundColor Yellow
}

Write-Host ""
Read-Host "Press Enter to exit"
'@ | Out-File -FilePath "$InstallPath\status.ps1" -Encoding UTF8

# logs.ps1
@'
# logs.ps1 - Просмотр логов

param([int]$Lines = 30)

$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptPath

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  📋 LAST $Lines LINES OF LOGS" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$logFile = "logs\web_server.log"
if (Test-Path $logFile) {
    Get-Content $logFile -Tail $Lines
} else {
    Write-Host "⚠️ Log file not found: $logFile" -ForegroundColor Yellow
}
Write-Host ""
Read-Host "Press Enter to exit"
'@ | Out-File -FilePath "$InstallPath\logs.ps1" -Encoding UTF8

# restart.ps1
@'
# restart.ps1 - Перезапуск бота

$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptPath

Write-Host ""
Write-Host "========================================" -ForegroundColor Magenta
Write-Host "  🔄 RESTARTING TRADING BOT" -ForegroundColor Magenta
Write-Host "========================================" -ForegroundColor Magenta
Write-Host ""

Write-Host "🛑 Stopping..." -ForegroundColor Yellow
& .\stop_bot.ps1

Write-Host "🚀 Starting..." -ForegroundColor Yellow
& .\start_bot.ps1 -Background

Write-Host "✅ RESTART COMPLETED" -ForegroundColor Green
'@ | Out-File -FilePath "$InstallPath\restart.ps1" -Encoding UTF8

# restart_with_logs.ps1
@'
# restart_with_logs.ps1 - Перезапуск с выводом логов

$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptPath

Write-Host ""
Write-Host "========================================" -ForegroundColor Magenta
Write-Host "  🔄 RESTARTING TRADING BOT (WITH LOGS)" -ForegroundColor Magenta
Write-Host "========================================" -ForegroundColor Magenta
Write-Host ""

Write-Host "🛑 Stopping bot..." -ForegroundColor Yellow
& .\stop_bot.ps1

Start-Sleep -Seconds 2

Write-Host "🚀 Starting bot with logs..." -ForegroundColor Yellow
Write-Host ""
Write-Host "Press Ctrl+C to stop" -ForegroundColor Cyan
Write-Host ""

& .\start_bot.ps1
'@ | Out-File -FilePath "$InstallPath\restart_with_logs.ps1" -Encoding UTF8

# README.txt
@'
========================================
   TRADING BOT - QUICK GUIDE
========================================

📁 INSTALLATION FOLDER: USERPROFILE\my-trading-bot

🔐 FIRST STEPS:
   1. Edit .env file with your tokens
   2. Add your TBANK_TOKEN

🚀 COMMANDS (Run in PowerShell):
   .\start_bot.ps1          - Run in console (see logs)
   .\start_bot.ps1 -Background - Run in background
   .\stop_bot.ps1           - Stop bot
   .\restart.ps1            - Restart bot
   .\status.ps1             - Check status
   .\logs.ps1               - View logs

📡 WEB SERVER:
   Health check: http://localhost:10000/health

📋 LOGS:
   Location: logs\web_server.log

========================================
'@ | Out-File -FilePath "$InstallPath\README.txt" -Encoding UTF8

Write-Host "   ✅ All scripts created" -ForegroundColor Green

# Final summary
Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "  ✅ INSTALLATION COMPLETED!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "📁 Installation path: $InstallPath" -ForegroundColor Cyan
Write-Host ""
Write-Host "🔐 NEXT STEPS:" -ForegroundColor Yellow
Write-Host ""
Write-Host "   1. Edit .env file with your token:" -ForegroundColor White
Write-Host "      notepad $InstallPath\.env" -ForegroundColor Cyan
Write-Host ""
Write-Host "   2. Test the bot:" -ForegroundColor White
Write-Host "      cd $InstallPath" -ForegroundColor DarkGray
Write-Host "      .\start_bot.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Host "   3. Check status:" -ForegroundColor White
Write-Host "      .\status.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""

pause