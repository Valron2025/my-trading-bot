# setup_task.ps1 - Setup autostart for Trading Bot
# Run as Administrator!

param(
    [string]$InstallPath = "C:\my-trading-bot"
)

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   SETUP AUTOSTART" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# Check admin rights
if (-NOT ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Host "ERROR: Run PowerShell as Administrator!" -ForegroundColor Red
    pause
    exit 1
}

Set-Location $InstallPath

# Create start_bot.bat
Write-Host "Creating start_bot.bat..." -ForegroundColor Yellow
@"
@echo off
cd /d $InstallPath
call venv\Scripts\activate
python web_server.py
"@ | Out-File -FilePath "$InstallPath\start_bot.bat" -Encoding ASCII
Write-Host "   OK: start_bot.bat created" -ForegroundColor Green

# Create web_server.py
Write-Host "Creating web_server.py..." -ForegroundColor Yellow
@"
import os
import sys
from flask import Flask, jsonify
from datetime import datetime

app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "service": "web"
    })

@app.route('/ping')
def ping():
    return "pong"

if __name__ == "__main__":
    port = 10000
    print(f"Starting web server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
"@ | Out-File -FilePath "$InstallPath\web_server.py" -Encoding UTF8
Write-Host "   OK: web_server.py created" -ForegroundColor Green

# Delete old task
Write-Host "Configuring Task Scheduler..." -ForegroundColor Yellow
schtasks /delete /tn "TradingBot" /f 2>$null | Out-Null

# Create new task
$Action = New-ScheduledTaskAction -Execute "$InstallPath\start_bot.bat"
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName "TradingBot" -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null

# Start task
Start-ScheduledTask -TaskName "TradingBot"
Write-Host "   OK: Task created and started" -ForegroundColor Green

# Check
Start-Sleep -Seconds 5
try {
    $response = Invoke-WebRequest -Uri "http://localhost:10000/health" -UseBasicParsing -TimeoutSec 5
    if ($response.StatusCode -eq 200) {
        Write-Host ""
        Write-Host "BOT IS WORKING!" -ForegroundColor Green
    }
} catch {
    Write-Host ""
    Write-Host "Bot is starting, check in 10 seconds:" -ForegroundColor Yellow
    Write-Host "   curl http://localhost:10000/health" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "SETUP COMPLETED!" -ForegroundColor Green
Write-Host ""
pause