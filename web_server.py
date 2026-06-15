#!/usr/bin/env python3
"""Web сервер для VPS - только health check и статус"""

import os
import sys
import time
import logging
from flask import Flask, jsonify
from datetime import datetime
import threading

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

print("=" * 60)
print("🌐 WEB SERVER STARTING (VPS MODE)")
print(f"   PID: {os.getpid()}")
print(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

# ========== ЗАПУСК TELEGRAM POLLING ==========
print("📱 STARTING TELEGRAM POLLING...")

_telegram_thread = None

try:
    from trading_bot.telegram.telegram_polling import start_polling_in_background

    _telegram_thread = start_polling_in_background()
    print("✅ TELEGRAM POLLING STARTED")
except ImportError as e:
    print(f"⚠️ Telegram module not found: {e}")
except Exception as e:
    print(f"⚠️ Error starting Telegram: {e}")

# ========== ЗАПУСК ТОРГОВОГО БОТА ==========
print("🚀 STARTING TRADING BOT...")

_trading_bot = None

try:
    from trading_bot import get_trading_bot

    _trading_bot = get_trading_bot()
    _trading_bot.start()
    print("✅ TRADING BOT STARTED")
except Exception as e:
    print(f"⚠️ Error starting trading bot: {e}")

app = Flask(__name__)

# Глобальные переменные
_bot_status = {
    'running': True,
    'cycle_count': 0,
    'positions': 0,
    'capital': 0,
    'last_update': None
}


@app.route('/')
@app.route('/health')
def health_check():
    """Базовая проверка здоровья"""
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "service": "trading-bot",
        "telegram_polling": _telegram_thread is not None and _telegram_thread.is_alive()
    }), 200


@app.route('/health/simple')
def health_simple():
    """Простая проверка для load balancer"""
    return "OK", 200


@app.route('/ping')
def ping():
    return "pong", 200


@app.route('/status')
def status():
    """Статус бота"""
    try:
        from trading_bot.api.tbank_client import tbank
        _, total, _ = tbank.get_available_funds()
        capital = total
    except:
        capital = 0

    return jsonify({
        "running": _bot_status.get('running', False),
        "capital": capital,
        "positions": _bot_status.get('positions', 0),
        "telegram_alive": _telegram_thread is not None and _telegram_thread.is_alive(),
        "timestamp": datetime.now().isoformat()
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 10000))

    print(f"\n🚀 STARTING WEB SERVER ON PORT {port}")
    print("=" * 60 + "\n")

    app.run(host='0.0.0.0', port=port, debug=False, threaded=False)