#!/usr/bin/env python3
"""Web сервер для VPS - только health check и статус"""

import os
import sys
import time
import logging
from flask import Flask, jsonify
from datetime import datetime
import threading
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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

# ========== ПРОВЕРКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==========
print("🔍 CHECKING ENVIRONMENT VARIABLES...")
tbank_token = os.getenv('TBANK_TOKEN')
tbank_account_id = os.getenv('TBANK_ACCOUNT_ID')
print(f"   TBANK_TOKEN: {'✅ SET' if tbank_token else '❌ NOT SET'}")
print(f"   TBANK_ACCOUNT_ID: {'✅ SET' if tbank_account_id else '❌ NOT SET'}")
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
    traceback.print_exc()

# ========== ЗАПУСК ТОРГОВОГО БОТА В ОТДЕЛЬНОМ ПОТОКЕ ==========
print("\n🚀 STARTING TRADING BOT...")
_trading_bot = None
_bot_thread = None


def run_bot():
    """Запуск торгового бота с полной отладкой"""
    global _trading_bot

    print("\n" + "=" * 60)
    print("🤖 RUN_BOT() STARTED")
    print("=" * 60)

    try:
        print("   📦 ШАГ 1: Импорт trading_bot...")
        from trading_bot import get_trading_bot
        print("   ✅ trading_bot импортирован успешно")

        print("   📦 ШАГ 2: Получение экземпляра бота...")
        _trading_bot = get_trading_bot()
        print(f"   ✅ Бот получен: {_trading_bot}")

        print("   🚀 ШАГ 3: Запуск бота...")
        _trading_bot.start()
        print("   ✅ TRADING BOT STARTED")

        print("\n" + "=" * 60)
        print("✅ БОТ УСПЕШНО ЗАПУЩЕН!")
        print("=" * 60)

    except ImportError as e:
        print(f"\n❌ ОШИБКА ИМПОРТА: {e}")
        print("   📋 Трассировка:")
        traceback.print_exc()

    except Exception as e:
        print(f"\n❌ ОШИБКА ЗАПУСКА БОТА: {e}")
        print("   📋 Трассировка:")
        traceback.print_exc()


# ✅ ЗАПУСКАЕМ БОТА В ОТДЕЛЬНОМ ПОТОКЕ
print("🔄 Создание потока для бота...")
_bot_thread = threading.Thread(target=run_bot, daemon=True)
_bot_thread.start()
print("✅ TRADING BOT THREAD STARTED")
print(f"   Поток запущен: {_bot_thread.is_alive()}")
print(f"   Имя потока: {_bot_thread.name}")

# ========== ОЖИДАНИЕ ЗАПУСКА БОТА ==========
print("\n⏳ Ожидание запуска бота (3 секунды)...")
time.sleep(3)

# Проверяем статус потока
if _bot_thread.is_alive():
    print("✅ Поток бота активен")
else:
    print("❌ Поток бота НЕ АКТИВЕН!")

print("=" * 60 + "\n")

# ========== FLASK APP ==========
app = Flask(__name__)

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
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "service": "trading-bot",
        "telegram_polling": _telegram_thread is not None and _telegram_thread.is_alive(),
        "bot_thread": _bot_thread is not None and _bot_thread.is_alive()
    }), 200


@app.route('/health/simple')
def health_simple():
    return "OK", 200


@app.route('/ping')
def ping():
    return "pong", 200


@app.route('/status')
def status():
    try:
        from trading_bot.api.tbank_client import tbank
        _, total, _ = tbank.get_available_funds()
        capital = total
    except Exception as e:
        print(f"⚠️ Error getting status: {e}")
        capital = 0

    return jsonify({
        "running": _bot_status.get('running', False),
        "capital": capital,
        "positions": _bot_status.get('positions', 0),
        "telegram_alive": _telegram_thread is not None and _telegram_thread.is_alive(),
        "bot_alive": _bot_thread is not None and _bot_thread.is_alive(),
        "timestamp": datetime.now().isoformat()
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 10000))
    print(f"\n🚀 STARTING WEB SERVER ON PORT {port}")
    print("=" * 60 + "\n")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)