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

# Запускаем с выводом логов (НЕ в фоне)
& .\start_bot.ps1