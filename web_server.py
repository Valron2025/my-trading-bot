#!/usr/bin/env python3
"""Web сервер для Render"""

# ПЕРВЫЙ импорт - SSL фикс
import ssl_fix  # Это должно быть ПЕРВЫМ!

# Затем все остальные импорты
import os
import sys
import logging
from flask import Flask, jsonify, request
from datetime import datetime
from threading import Lock
import gc
import time
import fcntl

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

print("=" * 60)
print("🌐 WEB SERVER STARTING")
print(f"   PID: {os.getpid()}")
print(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

# ========== ОПТИМИЗАЦИЯ ПАМЯТИ ==========
gc.set_threshold(500, 5, 2)
_last_gc_time = time.time()

# ========== ЗАЩИТА ОТ ДВОЙНОГО ЗАПУСКА ==========
_BOT_LOCK_FILE = "/tmp/trading_bot.lock"
_bot_lock_fd = None


def try_acquire_bot_lock():
    global _bot_lock_fd
    try:
        _bot_lock_fd = open(_BOT_LOCK_FILE, 'w')
        fcntl.flock(_bot_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(f"✅ Блокировка захвачена (PID={os.getpid()})")
        return True
    except (IOError, OSError):
        print(f"⏭️ Блокировка уже захвачена (PID={os.getpid()})")
        return False
    except Exception as e:
        print(f"⚠️ Ошибка блокировки: {e}")
        return True


# ========== ЗАПУСК ТОРГОВОГО БОТА ==========
print("🚀 ИНИЦИАЛИЗАЦИЯ ТОРГОВОГО БОТА...")

if try_acquire_bot_lock():
    from trading_bot import get_trading_bot

    trading_bot = get_trading_bot()
    trading_bot.start()
    print("✅ ТОРГОВЫЙ БОТ ЗАПУЩЕН")
else:
    print("⏭️ ТОРГОВЫЙ БОТ УЖЕ ЗАПУЩЕН")
    from trading_bot import get_trading_bot

    trading_bot = get_trading_bot()

# ========== ИНИЦИАЛИЗАЦИЯ TELEGRAM POLLING ==========
print("📱 ИНИЦИАЛИЗАЦИЯ TELEGRAM POLLING...")

_telegram_bot = None

try:
    from trading_bot.telegram.telegram_polling import start_polling_in_background

    start_polling_in_background()
    print("✅ TELEGRAM POLLING ЗАПУЩЕН")
    _telegram_bot = True
except Exception as e:
    print(f"⚠️ Ошибка инициализации Telegram: {e}")
# ========== КОНЕЦ ИНИЦИАЛИЗАЦИИ TELEGRAM ==========

app = Flask(__name__)

# Глобальные переменные
_bot_status = {
    'running': False,
    'cycle_count': 0,
    'positions': 0,
    'capital': 0,
    'last_update': None
}
_status_lock = Lock()


def set_telegram_bot(bot):
    """Установка глобальной ссылки на Telegram бота"""
    global _telegram_bot
    _telegram_bot = bot
    logger.info("✅ Telegram bot registered in web_server")
    print(f"📱 Telegram bot registered: {bot is not None}")


def get_telegram_bot():
    """Получение глобального экземпляра Telegram бота"""
    return _telegram_bot


def update_bot_status(running=None, cycle_count=None, positions=None, capital=None):
    """Обновление статуса бота с синхронизацией"""
    with _status_lock:
        if running is not None:
            _bot_status['running'] = running
            logger.info(f"📊 Bot running status updated: {running}")

            # Синхронизация с Prometheus
            try:
                from trading_bot.monitoring.prometheus_metrics import metrics
                if metrics and metrics.enabled:
                    metrics.update_bot_status(running)
            except Exception as e:
                logger.debug(f"Prometheus sync error: {e}")

        if cycle_count is not None:
            _bot_status['cycle_count'] = cycle_count
            logger.debug(f"📊 Cycle count: {cycle_count}")

        if positions is not None:
            _bot_status['positions'] = positions

        if capital is not None:
            _bot_status['capital'] = capital

        _bot_status['last_update'] = datetime.now().isoformat(timespec='seconds')

def periodic_gc():
    """Периодическая очистка памяти"""
    global _last_gc_time
    now = time.time()
    if now - _last_gc_time > 300:  # Каждые 5 минут
        _last_gc_time = now
        gc.collect()
        logger.info("🧹 GC выполнен")


# ========== ОСНОВНЫЕ ЭНДПОИНТЫ ==========

@app.route('/otc/positions')
def otc_positions():
    """Список OTC позиций, требующих ручного закрытия"""
    try:
        from trading_bot.risk.position_manager import position_manager
        otc = position_manager.get_otc_positions()
        return jsonify({
            'count': len(otc),
            'positions': otc,
            'message': 'Эти позиции необходимо закрыть вручную в приложении Т-Банк'
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/')
@app.route('/health')
def health_check():
    periodic_gc()
    response = {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "service": "web",
        "telegram_bot": _telegram_bot is not None
    }
    return jsonify(response), 200


@app.route('/health/simple')
def health_simple():
    return "OK", 200


@app.route('/ping')
def ping():
    return "pong", 200


@app.route('/health/detailed')
def health_detailed():
    try:
        from trading_bot.api.tbank_client import tbank
        _, total, _ = tbank.get_available_funds()
        capital = total
    except Exception as e:
        capital = 0
        logger.debug(f"Could not fetch capital: {e}")

    return jsonify({
        "status": "running",
        "telegram_bot": _telegram_bot is not None,
        "bot_running": _bot_status.get('running', False),
        "capital": capital,
        "positions": _bot_status.get('positions', 0),
        "cycle_count": _bot_status.get('cycle_count', 0),
        "timestamp": datetime.now().isoformat(),
        "environment": {
            "tbank_token_set": bool(os.getenv('TBANK_TOKEN')),
            "telegram_token_set": bool(os.getenv('TELEGRAM_TOKEN')),
            "python_version": sys.version,
        }
    }), 200


@app.route('/status')
def bot_status():
    with _status_lock:
        return jsonify({
            "running": _bot_status.get('running', False),
            "capital": _bot_status.get('capital', 0),
            "positions": _bot_status.get('positions', 0),
            "cycle_count": _bot_status.get('cycle_count', 0),
            "last_update": _bot_status.get('last_update'),
            "timestamp": datetime.now().isoformat()
        }), 200


@app.route('/internal/update_status', methods=['POST'])
def internal_update_status():
    try:
        data = request.get_json()
        if data:
            update_bot_status(
                running=data.get('running'),
                cycle_count=data.get('cycle_count'),
                positions=data.get('positions'),
                capital=data.get('capital')
            )
            logger.info(f"📡 Status updated via internal API: running={data.get('running')}")
        return jsonify({"success": True}), 200
    except Exception as e:
        logger.error(f"Error updating status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/metrics')
def metrics():
    return jsonify({
        "bot_running": 1 if _bot_status.get('running') else 0,
        "bot_cycle_count": _bot_status.get('cycle_count', 0),
        "bot_positions": _bot_status.get('positions', 0),
        "bot_capital": _bot_status.get('capital', 0),
        "timestamp": datetime.now().isoformat()
    }), 200


@app.route('/health/ready')
def readiness_check():
    return jsonify({"status": "ready"}), 200


@app.route('/health/liveness')
def liveness_check():
    return jsonify({"status": "alive"}), 200


@app.route('/info')
def info():
    return jsonify({
        "name": "Trading Bot",
        "version": "2.0.0",
        "description": "Автоматический торговый бот для T-Invest API",
        "features": [
            "LONG/SHORT торговля",
            "Адаптивные параметры под капитал",
            "Многоуровневый TP/SL",
            "Pre-market трейдинг",
            "Фундаментальный анализ",
            "Кэширование"
        ]
    }), 200


@app.route('/fundamental/status')
def fundamental_status():
    return jsonify({"status": "ok", "message": "Fundamental analysis available"}), 200


@app.route('/fundamental/<ticker>')
def fundamental_ticker(ticker):
    try:
        from trading_bot.analysis.fundamental_analyzer import fundamental_analyzer
        import asyncio

        async def get_metrics():
            return await fundamental_analyzer.fetch_metrics(ticker.upper())

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            metrics = loop.run_until_complete(get_metrics())
        finally:
            loop.close()

        if metrics:
            return jsonify({
                "ticker": ticker.upper(),
                "pe_ratio": metrics.pe_ratio,
                "pb_ratio": metrics.pb_ratio,
                "roe": metrics.roe,
                "dividend_yield": metrics.dividend_yield,
                "overall_score": metrics.overall_score,
                "recommendation": metrics.recommendation[0]
            }), 200
        else:
            return jsonify({"error": f"No data for {ticker}"}), 404
    except Exception as e:
        logger.error(f"Fundamental endpoint error: {e}")
        return jsonify({"error": str(e)}), 500


@app.errorhandler(404)
def not_found(error):
    logger.warning(f"404: {request.path}")
    return jsonify({"error": "Endpoint not found", "path": request.path}), 404


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"500 error: {error}")
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 10000))
    debug_mode = os.environ.get('DEBUG', 'false').lower() == 'true'

    print("\n" + "=" * 60)
    print(f"🚀 STARTING WEB SERVER")
    print(f"   Port: {port}")
    print(f"   Debug: {debug_mode}")
    print("=" * 60 + "\n")

    app.run(host='0.0.0.0', port=port, debug=debug_mode, threaded=False)