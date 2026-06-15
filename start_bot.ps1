# start_bot.ps1 - Запуск торгового бота с выводом логов в консоль
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
    Write-Host "   Please create .env with your TBANK_TOKEN" -ForegroundColor Yellow
    pause
    exit 1
}
Write-Host "✅ .env file found" -ForegroundColor Green

# Check venv
if (-not (Test-Path "venv\Scripts\python.exe")) {
    Write-Host "❌ ERROR: Virtual environment not found!" -ForegroundColor Red
    Write-Host "   Run: .\install_bot.ps1 first" -ForegroundColor Yellow
    pause
    exit 1
}
Write-Host "✅ Virtual environment found" -ForegroundColor Green

# Create logs folder
if (-not (Test-Path "logs")) {
    New-Item -ItemType Directory -Path "logs" -Force | Out-Null
    Write-Host "✅ Logs folder created" -ForegroundColor Green
}

# Stop old processes
Write-Host ""
Write-Host "🛑 Stopping old processes..." -ForegroundColor Yellow
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
if ($stopped -gt 0) {
    Write-Host "   ✅ Stopped $stopped processes" -ForegroundColor Green
    Start-Sleep -Seconds 2
} else {
    Write-Host "   ✅ No old processes found" -ForegroundColor Green
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  🚀 STARTING BOT" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

if ($Background) {
    # Фоновый режим - без вывода логов в консоль
    Write-Host "📡 Starting in BACKGROUND mode (logs go to file)..." -ForegroundColor Cyan
    $process = Start-Process -FilePath "venv\Scripts\python.exe" -ArgumentList "web_server.py" -WindowStyle Hidden -PassThru
    Write-Host ""
    Write-Host "✅ BOT STARTED IN BACKGROUND!" -ForegroundColor Green
    Write-Host "   PID: $($process.Id)" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "📊 Check status: .\status.ps1" -ForegroundColor Cyan
    Write-Host "📋 View logs: .\logs.ps1" -ForegroundColor Cyan
    Write-Host "🛑 Stop bot: .\stop_bot.ps1" -ForegroundColor Cyan
} else {
    # Обычный режим - с выводом логов в консоль
    Write-Host "📡 Starting in CONSOLE mode (logs will appear below)..." -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "========================================" -ForegroundColor DarkGray
    Write-Host ""

    # Запускаем Python и выводим логи в консоль
    & "venv\Scripts\python.exe" web_server.py
}