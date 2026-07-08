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

# ✅ ПРИНУДИТЕЛЬНЫЙ ВЫВОД ВСЕХ ОШИБОК В STDOUT
sys.stderr = sys.stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.DEBUG,
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
print("🔍 ENVIRONMENT VARIABLES:")
tbank_token = os.getenv('TBANK_TOKEN')
tbank_account_id = os.getenv('TBANK_ACCOUNT_ID')
print(f"   TBANK_TOKEN: {'✅ SET' if tbank_token else '❌ NOT SET'}")
print(f"   TBANK_ACCOUNT_ID: {'✅ SET' if tbank_account_id else '❌ NOT SET'}")
if tbank_token:
    print(f"   TBANK_TOKEN (first 20): {tbank_token[:20]}...")
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
_bot_started = False
_bot_error = None


def run_bot():
    """Запуск торгового бота с полной отладкой"""
    global _trading_bot, _bot_started, _bot_error

    print("\n" + "=" * 60)
    print("🤖 RUN_BOT() STARTED")
    print(f"   Thread: {threading.current_thread().name}")
    print("=" * 60)

    try:
        print("   📦 ШАГ 1: Импорт trading_bot...")
        sys.stdout.flush()
        from trading_bot import get_trading_bot
        print("   ✅ trading_bot импортирован успешно")
        sys.stdout.flush()

        print("   📦 ШАГ 2: Получение экземпляра бота...")
        sys.stdout.flush()
        _trading_bot = get_trading_bot()
        print(f"   ✅ Бот получен: {_trading_bot}")
        sys.stdout.flush()

        print("   🚀 ШАГ 3: Запуск бота...")
        sys.stdout.flush()
        _trading_bot.start()
        _bot_started = True
        print("   ✅ TRADING BOT STARTED")
        sys.stdout.flush()

        print("\n" + "=" * 60)
        print("✅ БОТ УСПЕШНО ЗАПУЩЕН!")
        print("=" * 60)
        sys.stdout.flush()

    except ImportError as e:
        _bot_error = f"ImportError: {e}"
        print(f"\n❌ ОШИБКА ИМПОРТА: {e}")
        traceback.print_exc()
        sys.stdout.flush()

    except Exception as e:
        _bot_error = f"Exception: {e}"
        print(f"\n❌ ОШИБКА ЗАПУСКА БОТА: {e}")
        traceback.print_exc()
        sys.stdout.flush()

    finally:
        print(f"\n📊 ИТОГ RUN_BOT:")
        print(f"   _bot_started: {_bot_started}")
        print(f"   _bot_error: {_bot_error}")
        print(f"   _trading_bot: {_trading_bot}")
        print("=" * 60)
        sys.stdout.flush()


# ✅ ЗАПУСКАЕМ БОТА В ОТДЕЛЬНОМ ПОТОКЕ
print("🔄 Создание потока для бота...")
_bot_thread = threading.Thread(target=run_bot, daemon=True, name="TradingBotThread")
_bot_thread.start()
print(f"✅ Поток создан: {_bot_thread.name}")
print(f"   Поток активен: {_bot_thread.is_alive()}")
sys.stdout.flush()

# ========== ОЖИДАНИЕ ЗАПУСКА БОТА ==========
print("\n⏳ Ожидание запуска бота (до 15 секунд)...")
for i in range(15):
    time.sleep(1)
    print(f"   ⏳ {i + 1}/15 - Поток активен: {_bot_thread.is_alive()}, Бот запущен: {_bot_started}")
    sys.stdout.flush()
    if _bot_started:
        print("✅ БОТ УСПЕШНО ЗАПУЩЕН!")
        break
    if _bot_error:
        print(f"❌ ОШИБКА ЗАПУСКА БОТА: {_bot_error}")
        break

print("\n📊 ФИНАЛЬНЫЙ СТАТУС:")
print(f"   Поток активен: {_bot_thread.is_alive()}")
print(f"   Бот запущен: {_bot_started}")
print(f"   Ошибка: {_bot_error}")
sys.stdout.flush()

# ========== FLASK APP ==========
app = Flask(__name__)


@app.route('/')
@app.route('/health')
def health_check():
    return jsonify({
        "status": "ok" if _bot_started else "degraded",
        "timestamp": datetime.now().isoformat(),
        "service": "trading-bot",
        "telegram_polling": _telegram_thread is not None and _telegram_thread.is_alive(),
        "bot_thread": _bot_thread is not None and _bot_thread.is_alive(),
        "bot_started": _bot_started,
        "bot_error": _bot_error
    }), 200 if _bot_started else 503


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
        capital = 0

    return jsonify({
        "running": _bot_started,
        "capital": capital,
        "positions": 0,
        "telegram_alive": _telegram_thread is not None and _telegram_thread.is_alive(),
        "bot_alive": _bot_thread is not None and _bot_thread.is_alive(),
        "bot_error": _bot_error,
        "timestamp": datetime.now().isoformat()
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 10000))
    print(f"\n🚀 STARTING WEB SERVER ON PORT {port}")
    print("=" * 60 + "\n")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)