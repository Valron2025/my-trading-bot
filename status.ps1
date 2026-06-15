# status.ps1 - Проверка статуса бота

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  📊 BOT STATUS" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "📅 Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray
Write-Host ""

# 1. Проверяем Python процессы
Write-Host "🐍 PYTHON PROCESSES:" -ForegroundColor Yellow
$processes = Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*my-trading-bot*" }

if ($processes) {
    Write-Host "   ✅ Found $($processes.Count) process(es):" -ForegroundColor Green
    foreach ($proc in $processes) {
        $mem = [math]::Round($proc.WorkingSet64 / 1MB, 2)
        Write-Host "      PID: $($proc.Id) | Memory: ${mem} MB" -ForegroundColor DarkGray
    }
} else {
    Write-Host "   ❌ No Python processes found" -ForegroundColor Red
}
Write-Host ""

# 2. Проверяем порт 10000
Write-Host "🔌 PORT 10000:" -ForegroundColor Yellow
$portTest = Test-NetConnection -ComputerName localhost -Port 10000 -WarningAction SilentlyContinue -ErrorAction SilentlyContinue

if ($portTest.TcpTestSucceeded) {
    Write-Host "   ✅ Port 10000: LISTENING" -ForegroundColor Green

    # Пробуем получить статус от веб-сервера
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:10000/health" -UseBasicParsing -TimeoutSec 5
        $data = $response.Content | ConvertFrom-Json
        Write-Host "   📡 Health check: OK" -ForegroundColor Green
        Write-Host "   🤖 Bot status: $($data.status)" -ForegroundColor Green
        if ($data.polling) {
            Write-Host "   📱 Telegram polling: ACTIVE" -ForegroundColor Green
        }
    } catch {
        Write-Host "   ⚠️ Web server responding but health check failed" -ForegroundColor Yellow
    }
} else {
    Write-Host "   ❌ Port 10000: CLOSED (bot not running)" -ForegroundColor Red
}
Write-Host ""

# 3. Проверяем задачу в планировщике
Write-Host "📋 SCHEDULED TASK:" -ForegroundColor Yellow
$task = schtasks /query /tn "TradingBot" /fo LIST 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "   ✅ Task exists" -ForegroundColor Green
    $task | Select-String "Status" | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
} else {
    Write-Host "   ⚠️ Task not found" -ForegroundColor Yellow
}
Write-Host ""

# 4. Проверяем последние логи
Write-Host "📋 LAST LOGS (last 5 lines):" -ForegroundColor Yellow
$logFile = "C:\my-trading-bot\logs\web_server.log"
if (Test-Path $logFile) {
    Get-Content $logFile -Tail 5 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }
} else {
    Write-Host "   ⚠️ Log file not found" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Read-Host "Press Enter to exit"