#!/bin/bash
echo "=========================================="
echo "🚀 STARTING TRADING BOT ON RENDER"
echo "=========================================="

export PYTHONPATH="/opt/render/project/src:$PYTHONPATH"

if [ -z "$TBANK_TOKEN" ]; then
    echo "❌ TBANK_TOKEN is not set!"
    exit 1
fi

# Устанавливаем зависимости (Render уже сделал это в Build Command)
# pip install -r requirements.txt --quiet

# Получаем порт от Render (ОБЯЗАТЕЛЬНО!)
PORT="${PORT:-10000}"
echo "📡 Using port: $PORT"

# Запускаем Flask в фоне с ПРАВИЛЬНЫМ портом
gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 1 --timeout 300 &
FLASK_PID=$!
echo "✅ Flask started with PID: $FLASK_PID on port $PORT"

# Небольшая пауза для запуска Flask
sleep 3

# Запускаем worker (торгового бота) в foreground
echo "🤖 Starting trading bot worker..."
python worker.py

# Если worker упал, убиваем Flask
kill $FLASK_PID 2>/dev/null