@echo off
chcp 65001 >nul 2>nul
setlocal enabledelayedexpansion

echo.
echo ========================================
echo   🚀 FORCE UPDATE TO GITHUB & RENDER
echo ========================================
echo.

:: Переход в папку проекта (✅ ИСПРАВЛЕНО)
cd /d E:\ДОКУМЕНТЫ\PROJECTS\my-trading-bot 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Project folder not found: E:\ДОКУМЕНТЫ\PROJECTS\my-trading-bot
    echo.
    pause
    exit /b 1
)
echo [OK] Project folder: %CD%
echo.

:: Проверка Git
git --version >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Git not found!
    echo Please install Git: https://git-scm.com/download/win
    pause
    exit /b 1
)

:: Инициализация репозитория если нужно
if not exist ".git" (
    echo Initializing repository...
    git init
    git remote add origin https://github.com/Valron2025/my-trading-bot.git
    echo [OK] Repository initialized
    echo.
)

:: Добавляем ВСЕ файлы
echo Adding all files...
git add -A
echo [OK] Files added
echo.

:: Создаём коммит
set "timestamp=%date% %time%"
set "commitMsg=force-update %timestamp%"
echo Committing: %commitMsg%
git commit -m "%commitMsg%" --allow-empty
echo [OK] Commit created
echo.

:: ПРИНУДИТЕЛЬНЫЙ PUSH
echo Force pushing to GitHub...
git push origin main --force

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Force push failed!
    echo Trying alternative method...
    git push origin main --force-with-lease
    if %errorlevel% neq 0 (
        echo [ERROR] Push failed completely!
        pause
        exit /b 1
    )
)

echo.
echo ========================================
echo   ✅ SUCCESS!
echo ========================================
echo.
echo Last commit:
git log -1 --oneline
echo.
echo 🔗 GitHub: https://github.com/Valron2025/my-trading-bot
echo 🤖 Render will redeploy automatically
echo.
pause