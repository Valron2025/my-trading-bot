# restart.ps1 - Перезапуск бота
Write-Host "==========================================" -ForegroundColor Magenta
Write-Host "  🔄 RESTARTING TRADING BOT" -ForegroundColor Magenta
Write-Host "==========================================" -ForegroundColor Magenta
Write-Host ""

Write-Host "Stopping bot..." -ForegroundColor Cyan
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Stop-ScheduledTask -TaskName "TradingBot" -ErrorAction SilentlyContinue
Remove-Item C:\my-trading-bot\trading_bot.lock -Force -ErrorAction SilentlyContinue

Start-Sleep -Seconds 3

Write-Host "Starting bot..." -ForegroundColor Cyan
Start-ScheduledTask -TaskName "TradingBot" -ErrorAction SilentlyContinue

Start-Sleep -Seconds 5

Write-Host ""
Write-Host "✅ BOT RESTARTED" -ForegroundColor Green
Write-Host ""

# Проверяем статус
try {
    $response = Invoke-WebRequest -Uri "http://localhost:10000/health" -UseBasicParsing -TimeoutSec 5
    Write-Host "Status: OK" -ForegroundColor Green
} catch {
    Write-Host "Status: Starting..." -ForegroundColor Yellow
}

Write-Host ""
Read-Host "Press Enter to exit"