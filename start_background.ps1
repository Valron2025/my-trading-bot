# start_background.ps1 - Запуск бота в фоне (без вывода логов в консоль)
# НЕ ТРЕБУЕТ АДМИНИСТРАТОРА

$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptPath

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  🚀 STARTING BOT IN BACKGROUND" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

# Используем start_bot.ps1 с флагом Background
& .\start_bot.ps1 -Background