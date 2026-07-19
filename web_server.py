#!/usr/bin/env python3
"""Web сервер для VPS - health check, статус и метрики Prometheus"""

# ============================================
# 🔧 ФИКС ДЛЯ RENDER: ПРИНУДИТЕЛЬНАЯ УСТАНОВКА АДРЕСА API
# ============================================
import os
import socket
import sys
import time
import logging

# 1. Принудительная установка переменных окружения ДО ВСЕХ ИМПОРТОВ
os.environ['TBANK_API_URL'] = 'invest-public-api.tbank.ru:443'
os.environ['TINKOFF_API_URL'] = 'invest-public-api.tbank.ru:443'
os.environ['INVEST_API_URL'] = 'invest-public-api.tbank.ru:443'
os.environ['GRPC_DNS_RESOLVER'] = 'native'
os.environ['GRPC_VERBOSITY'] = 'ERROR'

# 2. Проверка DNS резолвинга
try:
    ip = socket.gethostbyname('invest-public-api.tbank.ru')
    print(f"✅ DNS резолвинг: invest-public-api.tbank.ru → {ip}")
except Exception as e:
    print(f"❌ DNS ошибка: {e}")

# 3. Попытка очистить DNS кэш
try:
    import dns.resolver
    dns.resolver.cache.flush()
    print("✅ DNS кэш очищен")
except:
    pass

print("✅ Render окружение настроено для T-Bank API")
# ============================================

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
print("🔍 ENVIRONMENT VARIABLES:")
tbank_token = os.getenv('TBANK_TOKEN')
tbank_account_id = os.getenv('TBANK_ACCOUNT_ID')
tbank_api_url = os.getenv('TBANK_API_URL')
print(f"   TBANK_TOKEN: {'✅ SET' if tbank_token else '❌ NOT SET'}")
print(f"   TBANK_ACCOUNT_ID: {'✅ SET' if tbank_account_id else '❌ NOT SET'}")
print(f"   TBANK_API_URL: {'✅ SET' if tbank_api_url else '❌ NOT SET'}")
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
_bot_start_time = time.time()


def run_bot():
    """Запуск торгового бота с полной отладкой"""
    global _trading_bot, _bot_started, _bot_error, _bot_start_time

    print("\n" + "=" * 60)
    print("🤖 RUN_BOT() STARTED")
    print(f"   Thread: {threading.current_thread().name}")
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
        _bot_started = True
        _bot_start_time = time.time()
        print("   ✅ TRADING BOT STARTED")

        try:
            from trading_bot.monitoring.prometheus_metrics import prometheus_metrics
            prometheus_metrics.set_bot_status(1)
            prometheus_metrics.set_bot_uptime(0)
            print("   ✅ Prometheus метрики обновлены (бот запущен)")
        except Exception as e:
            print(f"   ⚠️ Ошибка обновления метрик: {e}")

        print("\n" + "=" * 60)
        print("✅ БОТ УСПЕШНО ЗАПУЩЕН!")
        print("=" * 60)

    except ImportError as e:
        _bot_error = f"ImportError: {e}"
        print(f"\n❌ ОШИБКА ИМПОРТА: {e}")
        traceback.print_exc()

    except Exception as e:
        _bot_error = f"Exception: {e}"
        print(f"\n❌ ОШИБКА ЗАПУСКА БОТА: {e}")
        traceback.print_exc()

    finally:
        print(f"\n📊 ИТОГ RUN_BOT:")
        print(f"   _bot_started: {_bot_started}")
        print(f"   _bot_error: {_bot_error}")
        print(f"   _trading_bot: {_trading_bot}")
        print("=" * 60)


# ✅ ЗАПУСКАЕМ БОТА В ОТДЕЛЬНОМ ПОТОКЕ
print("🔄 Создание потока для бота...")
_bot_thread = threading.Thread(target=run_bot, daemon=True, name="TradingBotThread")
_bot_thread.start()
print(f"✅ Поток создан: {_bot_thread.name}")

# ========== ОЖИДАНИЕ ЗАПУСКА БОТА (60 СЕКУНД) ==========
print("\n⏳ Ожидание запуска бота (до 60 секунд)...")
for i in range(60):
    time.sleep(1)
    if _bot_started:
        print("✅ БОТ УСПЕШНО ЗАПУЩЕН!")
        break
    if _bot_error:
        print(f"❌ ОШИБКА ЗАПУСКА БОТА: {_bot_error}")
        break
    if i % 5 == 0:
        print(f"   ⏳ Ожидание... {i + 1}/60")

print("\n📊 ФИНАЛЬНЫЙ СТАТУС:")
print(f"   Поток активен: {_bot_thread.is_alive()}")
print(f"   Бот запущен: {_bot_started}")
print(f"   Ошибка: {_bot_error}")
print("=" * 60 + "\n")

# ========== FLASK APP ==========
app = Flask(__name__)

_status_cache = {}
_status_cache_time = 0
_STATUS_CACHE_TTL = 30

_cached_capital = 0
_cached_capital_time = 0
_CAPITAL_CACHE_TTL = 30

_cached_positions = 0
_cached_positions_time = 0


def get_cached_capital():
    global _cached_capital, _cached_capital_time
    now = time.time()
    if now - _cached_capital_time < _CAPITAL_CACHE_TTL:
        return _cached_capital

    try:
        result = [None]
        error = [None]

        def _get_balance():
            try:
                from trading_bot.api.tbank_client import tbank
                _, total, _ = tbank.get_available_funds()
                result[0] = total
            except Exception as e:
                error[0] = e

        thread = threading.Thread(target=_get_balance, daemon=True)
        thread.start()
        thread.join(timeout=2.0)

        if thread.is_alive():
            capital = _cached_capital
        elif error[0]:
            capital = _cached_capital
        else:
            capital = result[0] or _cached_capital

    except Exception:
        capital = _cached_capital

    _cached_capital = capital
    _cached_capital_time = time.time()
    return capital


def get_cached_positions():
    global _cached_positions, _cached_positions_time
    now = time.time()
    if now - _cached_positions_time < _CAPITAL_CACHE_TTL:
        return _cached_positions

    try:
        from trading_bot.api.tbank_client import tbank
        positions = tbank.get_positions()
        count = len(positions)
        _cached_positions = count
        _cached_positions_time = time.time()
        return count
    except Exception:
        return _cached_positions


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
        "bot_error": _bot_error,
        "api_url": os.getenv('TBANK_API_URL', 'not set')
    }), 200 if _bot_started else 503


@app.route('/health/simple')
def health_simple():
    return "OK", 200


@app.route('/ping')
def ping():
    return "pong", 200


@app.route('/status')
def status():
    global _status_cache, _status_cache_time

    now = time.time()
    if now - _status_cache_time < _STATUS_CACHE_TTL:
        return jsonify(_status_cache), 200

    capital = get_cached_capital()
    positions = get_cached_positions()

    response = {
        "running": _bot_started,
        "capital": capital,
        "positions": positions,
        "telegram_alive": _telegram_thread is not None and _telegram_thread.is_alive(),
        "bot_alive": _bot_thread is not None and _bot_thread.is_alive(),
        "bot_error": _bot_error,
        "timestamp": datetime.now().isoformat()
    }

    _status_cache = response
    _status_cache_time = time.time()

    return jsonify(response), 200


@app.route('/metrics')
def metrics():
    try:
        from trading_bot.monitoring.prometheus_metrics import prometheus_metrics

        prometheus_metrics.set_bot_status(1 if _bot_started else 0)

        if _bot_started:
            prometheus_metrics.set_bot_uptime(time.time() - _bot_start_time)

        try:
            capital = get_cached_capital()
            if capital > 0:
                prometheus_metrics.set_portfolio_value(float(capital))
                prometheus_metrics.set_portfolio_cash(float(capital * 0.5))

            positions = get_cached_positions()
            prometheus_metrics.set_positions_count(float(positions))
        except Exception:
            pass

        if _trading_bot and _bot_started:
            try:
                from trading_bot.api.tbank_client import tbank
                positions_data = tbank.get_positions()
                total_pnl = 0.0
                for pos in positions_data:
                    figi = pos.get('figi')
                    if figi:
                        qty = pos.get('quantity', 0)
                        avg = pos.get('avg_price', 0)
                        cur = tbank.get_current_price(figi)
                        if cur and avg > 0:
                            if qty < 0:
                                pnl = (avg - cur) * abs(qty)
                            else:
                                pnl = (cur - avg) * qty
                            total_pnl += pnl

                if total_pnl != 0:
                    prometheus_metrics.set_daily_pnl(float(total_pnl))

                margin_info = tbank.get_margin_info()
                margin_rate = margin_info.get('margin_rate', 0)
                if margin_rate > 0:
                    prometheus_metrics.set_margin_rate(float(margin_rate))

            except Exception:
                pass

        prometheus_metrics.update_system_metrics()

        return prometheus_metrics.get_metrics(), 200, {
            'Content-Type': 'text/plain; version=0.0.4; charset=utf-8'
        }

    except ImportError as e:
        return f"# Prometheus not available: {e}\n", 200, {'Content-Type': 'text/plain'}
    except Exception as e:
        return f"# Error: {e}\n", 500, {'Content-Type': 'text/plain'}


@app.route('/metrics/summary')
def metrics_summary():
    try:
        from trading_bot.monitoring.prometheus_metrics import prometheus_metrics
        return jsonify(prometheus_metrics.get_metrics_summary()), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def background_metrics_updater():
    while True:
        try:
            if _bot_started and _trading_bot:
                from trading_bot.monitoring.prometheus_metrics import prometheus_metrics

                prometheus_metrics.set_bot_status(1)
                prometheus_metrics.set_bot_uptime(time.time() - _bot_start_time)
                prometheus_metrics.update_system_metrics()

                try:
                    from trading_bot.api.tbank_client import tbank
                    _, total, _ = tbank.get_available_funds()
                    if total > 0:
                        prometheus_metrics.set_portfolio_value(float(total))

                    positions = tbank.get_positions()
                    prometheus_metrics.set_positions_count(float(len(positions)))
                except Exception:
                    pass

                if hasattr(_trading_bot, 'trading_loop') and _trading_bot.trading_loop:
                    cycle_count = getattr(_trading_bot.trading_loop, '_cycle_count', 0)
                    if cycle_count > 0:
                        prometheus_metrics.set_trading_cycle_count(cycle_count)

        except Exception:
            pass

        time.sleep(15)


_metrics_updater_thread = threading.Thread(target=background_metrics_updater, daemon=True)
_metrics_updater_thread.start()
print("✅ Фоновый поток обновления метрик запущен")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"\n🚀 STARTING WEB SERVER ON PORT {port}")
    print("=" * 60 + "\n")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)