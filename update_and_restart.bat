@echo off
cd /d C:\my-trading-bot
echo Pulling latest changes...
git pull origin main
echo Restarting bot...
net stop TradingBot
timeout /t 2
net start TradingBot
echo Done!