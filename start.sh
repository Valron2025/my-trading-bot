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

# ============================================
# ОЧИСТКА КЭША ДЛЯ RENDER
# ============================================
echo "🧹 Очистка кэша..."

# Очистка Python кэша
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find . -type f -name "*.pyc" -delete 2>/dev/null

# Очистка gRPC кэша
rm -rf /tmp/grpc* 2>/dev/null
rm -rf ~/.cache/grpc* 2>/dev/null

# Принудительная установка переменных
export TBANK_API_URL="invest-public-api.tbank.ru:443"
export T_INVEST_API_URL="invest-public-api.tbank.ru:443"
export GRPC_DNS_RESOLVER="native"
export GRPC_VERBOSITY="ERROR"

# Проверка наличия gunicorn
if ! command -v gunicorn &> /dev/null; then
    echo "❌ gunicorn не найден, устанавливаю..."
    pip install gunicorn
fi

# Проверка наличия certifi
python -c "import certifi" 2>/dev/null || pip install certifi
echo "✅ certifi установлен"

echo "✅ Кэш очищен, переменные установлены"
# ============================================

# ============================================
# SSL НАСТРОЙКА ДЛЯ RENDER
# ============================================
echo "🔐 Настройка SSL для Render..."

# Получаем путь к сертификатам
CERT_PATH=$(python -c "import certifi; print(certifi.where())")
export SSL_CERT_FILE=$CERT_PATH
export REQUESTS_CA_BUNDLE=$CERT_PATH
export GRPC_SSL_CIPHER_SUITES='HIGH+ECDSA+HIGH'

echo "   SSL_CERT_FILE: $SSL_CERT_FILE"
echo "🔐 SSL настроен"
# ============================================

# ============================================
# 🔧 ФИКС ДЛЯ RENDER: ПРИНУДИТЕЛЬНАЯ УСТАНОВКА ПЕРЕМЕННЫХ
# ============================================
export TBANK_API_URL="invest-public-api.tbank.ru:443"
export TINKOFF_API_URL="invest-public-api.tbank.ru:443"
export INVEST_API_URL="invest-public-api.tbank.ru:443"
export GRPC_DNS_RESOLVER="native"
export GRPC_VERBOSITY="ERROR"

# Очистка кэша DNS (если возможно)
if command -v resolvectl &> /dev/null; then
    resolvectl flush-caches 2>/dev/null || true
    echo "✅ DNS кэш очищен (resolvectl)"
fi
# ============================================

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

echo "📡 TBANK_API_URL: $TBANK_API_URL"

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
    --timeout 600 \
    --max-requests 500 \
    --max-requests-jitter 50 \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    --preload