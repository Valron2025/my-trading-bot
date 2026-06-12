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

# ========== SSL FIX FOR T-BANK API ==========
# Отключаем проверку SSL сертификатов для gRPC
export GRPC_SSL_CIPHER_SUITES=HIGH
export GRPC_VERBOSITY=ERROR
export GRPC_TRACE=

# Отключаем проверку для HTTPS запросов
export CURL_CA_BUNDLE=""
export REQUESTS_CA_BUNDLE=""
export SSL_CERT_FILE=""
export NODE_TLS_REJECT_UNAUTHORIZED=0

# Для Python urllib3
export PYTHONHTTPSVERIFY=0

echo "🔓 SSL проверка ОТКЛЮЧЕНА для Render (T-Bank API fix)"

# ========== ПОДАВЛЕНИЕ ПРЕДУПРЕЖДЕНИЙ ==========
export PYTHONWARNINGS="ignore:Unverified HTTPS request"

# ========== НАСТРОЙКА PYTHONPATH ==========
export PYTHONPATH="/opt/render/project/src:$PYTHONPATH"
echo "📁 PYTHONPATH: $PYTHONPATH"

# ========== ПРОВЕРКА ПЕРЕМЕННЫХ ==========
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

# Запуск Gunicorn с web_server
gunicorn web_server:app \
    --bind 0.0.0.0:$PORT \
    --workers 1 \
    --threads 1 \
    --timeout 300 \
    --access-logfile - \
    --error-logfile - \
    --log-level info