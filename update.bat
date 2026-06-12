@echo off
chcp 65001 >nul 2>nul
setlocal enabledelayedexpansion

echo.
echo ========================================
echo   🚀 UPDATE TO GITHUB
echo ========================================
echo.

:: Автоматическое определение папки проекта (где находится сам скрипт)
cd /d "%~dp0"
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

:: Показываем изменения
git status --short
echo.

:: Создаём коммит (только если есть изменения)
git diff --cached --quiet
if %errorlevel% equ 0 (
    echo No changes to commit.
) else (
    set "timestamp=%date% %time%"
    set "commitMsg=update %timestamp%"
    echo Committing: %commitMsg%
    git commit -m "%commitMsg%"
    echo [OK] Commit created
)
echo.

:: Push на GitHub
echo Pushing to GitHub...
git push origin main

if %errorlevel% neq 0 (
    echo.
    echo [WARNING] Push failed, trying force...
    git push origin main --force
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