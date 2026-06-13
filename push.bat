@echo off
cd /d F:\PROJECTS\my-trading-bot
echo ========================================
echo   FORCE PUSH TO GITHUB
echo ========================================
echo.
echo Current folder: %CD%
echo.

:: Инициализация если нужно
if not exist ".git" (
    echo Initializing git repository...
    git init
    git remote add origin https://github.com/Valron2025/my-trading-bot.git
    echo.
)

:: Добавляем всё
git add .
echo Added all files
echo.

:: Коммит
git commit -m "force-update %date% %time%" --allow-empty
echo Commit created
echo.

:: Force push
git push origin main --force

echo.
echo ========================================
echo   DONE!
echo ========================================
pause