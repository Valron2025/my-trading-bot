# logs.ps1 - Просмотр логов бота

param(
    [int]$Lines = 30,
    [switch]$Follow = $false
)

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  📋 BOT LOGS" -ForegroundColor Cyan
Write-Host "========================================