# stop_bot.ps1 - Остановка торгового бота
# Run as Administrator!

Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  🛑 STOPPING TRADING BOT" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""
Write-Host "📅 Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray
Write-Host ""

# Переходим в папку проекта
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptPath

# 1. Останавливаем задачу в планировщике
Write-Host "📋 Stopping scheduled task..." -ForegroundColor Cyan
Stop-ScheduledTask -TaskName "TradingBot" -ErrorAction SilentlyContinue
Write-Host "   ✅ Scheduled task stopped" -ForegroundColor Green

# 2. Останавливаем все Python процессы из нашей папки
Write-Host "🐍 Stopping Python processes..." -ForegroundColor Cyan

$stopped = 0
$processes = Get-Process python -ErrorAction SilentlyContinue

foreach ($proc in $processes) {
    try {
        # Проверяем, относится ли процесс к нашему боту
        $procPath = $proc.Path
        if ($procPath -like "*my-trading-bot*") {
            Write-Host "   Stopping PID $($proc.Id): $procPath" -ForegroundColor DarkGray
            $proc.Kill()
            $stopped++
        }
    } catch {
        # Игнорируем ошибки
    }
}

Write-Host "   ✅ Stopped $stopped processes" -ForegroundColor Green

# 3. Убиваем процессы на порту 10000
Write-Host "🔌 Freeing port 10000..." -ForegroundColor Cyan
$connections = netstat -ano | findstr ":10000" | findstr "LISTENING"
if ($connections) {
    $connections | ForEach-Object {
        $parts = $_ -split '\s+'
        $pid = $parts[-1]
        if ($pid -match '^\d+$') {
            try {
                Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
                Write-Host "   ✅ Killed process on port 10000 (PID: $pid)" -ForegroundColor Green
            } catch {
                # Игнорируем
            }
        }
    }
}

# 4. Удаляем lock файлы
Write-Host "🗑️ Removing lock files..." -ForegroundColor Cyan
Remove-Item "$scriptPath\trading_bot.lock" -Force -ErrorAction SilentlyContinue
Remove-Item "$env:TEMP\trading_bot.lock" -Force -ErrorAction SilentlyContinue
Write-Host "   ✅ Lock files removed" -ForegroundColor Green

# 5. Ждём завершения
Start-Sleep -Seconds 2

# 6. Финальная проверка
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  ✅ BOT STOPPED" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

# Проверяем, что всё остановлено
$remaining = Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*my-trading-bot*" }
if ($remaining) {
    Write-Host "⚠️ Warning: Some processes still running:" -ForegroundColor Yellow
    foreach ($proc in $remaining) {
        Write-Host "   PID $($proc.Id): $($proc.Path)" -ForegroundColor DarkGray
    }
} else {
    Write-Host "✅ All processes stopped successfully" -ForegroundColor Green
}

Write-Host ""
Read-Host "Press Enter to exit"