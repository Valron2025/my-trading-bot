#!/bin/bash
# ============================================
# START SCRIPT FOR RENDER DEPLOYMENT
# ============================================

echo "=========================================="
echo "🚀 STARTING TRADING BOT ON RENDER"
echo "=========================================="
echo "Time: $(date)"
echo "User: $(whoami)"
echo "PWD: $(pwd)"
echo "=========================================="

# Настройка PYTHONPATH
export PYTHONPATH="/opt/render/project/src:$PYTHONPATH"
echo "📁 PYTHONPATH: $PYTHONPATH"

# Проверка переменных окружения
echo ""
echo "📋 Checking environment variables..."

if [ -z "$TBANK_TOKEN" ]; then
    echo "❌ TBANK_TOKEN is not set!"
    exit 1
else
    echo "✅ TBANK_TOKEN is set"
fi

if [ -z "$TELEGRAM_TOKEN" ]; then
    echo "⚠️ TELEGRAM_TOKEN is not set (optional)"
else
    echo "✅ TELEGRAM_TOKEN is set"
fi

PORT="${PORT:-10000}"
echo "📡 PORT: $PORT"

echo ""
echo "=========================================="
echo "🚀 STARTING WEB SERVER (GUNICORN)"
echo "=========================================="

# Запуск Gunicorn с web_server (только один worker)
gunicorn web_server:app \
    --bind 0.0.0.0:$PORT \
    --workers 1 \
    --threads 1 \
    --timeout 300 \
    --access-logfile - \
    --error-logfile - \
    --log-level info

# ========== ВАЖНО: worker.py НЕ ЗАПУСКАЕТСЯ ОТДЕЛЬНО ==========
# Бот уже запущен внутри web_server.py через блокировку
# Telegram polling также запущен внутри web_server.py