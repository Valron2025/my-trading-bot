#!/usr/bin/env python3
"""Web сервер для Render - с поддержкой Telegram webhook и health check"""

import os
import sys
import logging
from flask import Flask, jsonify, request
from datetime import datetime
from threading import Lock
import gc
import time
import signal
import atexit

# Кроссплатформенная блокировка
import platform

if platform.system() == 'Windows':
    import msvcrt
    import tempfile


    class WindowsFileLock:
        def __init__(self, lock_file):
            self.lock_file = lock_file
            self.handle = None

        def acquire(self):
            try:
                self.handle = open(self.lock_file, 'w+')
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except (IOError, OSError):
                if self.handle:
                    self.handle.close()
                return False

        def release(self):
            if self.handle:
                try:
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                    self.handle.close()
                except:
                    pass
                self.handle = None
else:
    import fcntl


    class UnixFileLock:
        def __init__(self, lock_file):
            self.lock_file = lock_file
            self.fd = None

        def acquire(self):
            try:
                self.fd = open(self.lock_file, 'w')
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except (IOError, OSError):
                if self.fd:
                    self.fd.close()
                return False

        def release(self):
            if self.fd:
                try:
                    fcntl.flock(self.fd, fcntl.LOCK_UN)
                    self.fd.close()
                except:
                    pass
                self.fd = None

# Выбираем нужную реализацию
if platform.system() == 'Windows':
    FileLock = WindowsFileLock
    import tempfile
else:
    FileLock = UnixFileLock

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Добавляем файловое логирование
try:
    file_handler = logging.FileHandler('web_server.log')
    file_handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
    logger.addHandler(file_handler)
except Exception as e:
    print(f"⚠️ Cannot create log file: {e}")

print("=" * 60)
print("🌐 WEB SERVER STARTING")
print(f"   Platform: {platform.system()}")
print(f"   PID: {os.getpid()}")
print(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

# ========== ОПТИМИЗАЦИЯ ПАМЯТИ ==========
gc.set_threshold(500, 5, 2)
_last_gc_time = time.time()

# ========== ЗАЩИТА ОТ ДВОЙНОГО ЗАПУСКА ==========
if platform.system() == 'Windows':
    _BOT_LOCK_FILE = os.path.join(tempfile.gettempdir(), "trading_bot.lock")
else:
    _BOT_LOCK_FILE = "/tmp/trading_bot.lock"

_bot_lock = None
_trading_bot = None


def try_acquire_bot_lock():
    global _bot_lock
    try:
        _bot_lock = FileLock(_BOT_LOCK_FILE)
        if _bot_lock.acquire():
            print(f"✅ Блокировка захвачена (PID={os.getpid()})")
            return True
        else:
            print(f"⏭️ Блокировка уже захвачена (PID={os.getpid()})")
            return False
    except Exception as e:
        print(f"⚠️ Ошибка блокировки: {e}")
        return True  # Продолжаем выполнение даже при ошибке блокировки


def cleanup_resources():
    """Освобождение ресурсов при завершении"""
    global _bot_lock, _trading_bot
    print("\n🧹 Cleaning up resources...")

    # Останавливаем торгового бота
    if _trading_bot:
        try:
            print("🛑 Stopping trading bot...")
            _trading_bot.stop()
            print("✅ Trading bot stopped")
        except Exception as e:
            print(f"⚠️ Error stopping bot: {e}")

    # Освобождаем блокировку
    if _bot_lock:
        try:
            _bot_lock.release()
            print("🔓 Lock released")
        except Exception as e:
            print(f"⚠️ Error releasing lock: {e}")

    print("👋 Web server shutdown complete")


def signal_handler(signum, frame):
    """Обработчик сигналов для graceful shutdown"""
    print(f"\n⚠️ Received signal {signum}")
    cleanup_resources()
    sys.exit(0)


# Регистрируем обработчики для graceful shutdown
atexit.register(cleanup_resources)
signal.signal(signal.SIGINT, signal_handler)
if hasattr(signal, 'SIGTERM'):
    signal.signal(signal.SIGTERM, signal_handler)

# ========== ЗАПУСК ТОРГОВОГО БОТА ==========
print("🚀 ИНИЦИАЛИЗАЦИЯ ТОРГОВОГО БОТА...")

if try_acquire_bot_lock():
    from trading_bot import get_trading_bot

    _trading_bot = get_trading_bot()
    _trading_bot.start()
    print("✅ ТОРГОВЫЙ БОТ ЗАПУЩЕН")
else:
    print("⏭️ ТОРГОВЫЙ БОТ УЖЕ ЗАПУЩЕН")
    from trading_bot import get_trading_bot

    _trading_bot = get_trading_bot()

# ========== ИНИЦИАЛИЗАЦИЯ TELEGRAM POLLING ==========
print("📱 ИНИЦИАЛИЗАЦИЯ TELEGRAM POLLING...")

_telegram_bot = None
_telegram_polling_thread = None

try:
    from trading_bot.telegram.telegram_polling import start_polling_in_background

    # Запускаем polling в фоновом режиме
    _telegram_polling_thread = start_polling_in_background()
    print("✅ TELEGRAM POLLING ЗАПУЩЕН")
    _telegram_bot = True
except ImportError as e:
    print(f"⚠️ Telegram module not found: {e}")
except Exception as e:
    print(f"⚠️ Ошибка инициализации Telegram: {e}")
# ========== КОНЕЦ ИНИЦИАЛИЗАЦИИ TELEGRAM ==========

app = Flask(__name__)


# Добавляем CORS поддержку для API
@app.after_request
def add_cors_headers(response):
    """Добавляем CORS headers для всех ответов"""
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response


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
        logger.error(f"OTC positions error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/')
@app.route('/health')
def health_check():
    """Базовая проверка здоровья"""
    periodic_gc()
    response = {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "service": "web",
        "telegram_bot": _telegram_bot is not None,
        "platform": platform.system()
    }
    return jsonify(response), 200


@app.route('/health/simple')
def health_simple():
    """Простая проверка для load balancer"""
    return "OK", 200


@app.route('/ping')
def ping():
    """Ping endpoint"""
    return "pong", 200


@app.route('/health/detailed')
def health_detailed():
    """Детальная проверка здоровья"""
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
            "platform": platform.system()
        }
    }), 200


@app.route('/status')
def bot_status():
    """Статус бота"""
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
    """Внутренний эндпоинт для обновления статуса"""
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
    """Метрики для Prometheus"""
    return jsonify({
        "bot_running": 1 if _bot_status.get('running') else 0,
        "bot_cycle_count": _bot_status.get('cycle_count', 0),
        "bot_positions": _bot_status.get('positions', 0),
        "bot_capital": _bot_status.get('capital', 0),
        "timestamp": datetime.now().isoformat()
    }), 200


@app.route('/health/ready')
def readiness_check():
    """Проверка готовности для Kubernetes"""
    # Проверяем, что бот инициализирован
    is_ready = _trading_bot is not None
    return jsonify({"status": "ready" if is_ready else "not_ready"}), 200 if is_ready else 503


@app.route('/health/liveness')
def liveness_check():
    """Проверка живости для Kubernetes"""
    return jsonify({"status": "alive"}), 200


@app.route('/info')
def info():
    """Информация о боте"""
    return jsonify({
        "name": "Trading Bot",
        "version": "3.0.0",
        "description": "Автоматический торговый бот для T-Invest API",
        "features": [
            "LONG/SHORT торговля",
            "Адаптивные параметры под капитал",
            "Многоуровневый TP/SL",
            "Pre-market трейдинг",
            "Фундаментальный анализ",
            "Новостной анализ",
            "Кэширование"
        ],
        "platform": platform.system()
    }), 200


@app.route('/fundamental/status')
def fundamental_status():
    """Статус фундаментального анализатора"""
    return jsonify({"status": "ok", "message": "Fundamental analysis available"}), 200


@app.route('/fundamental/<ticker>')
def fundamental_ticker(ticker):
    """Получить фундаментальные данные по тикеру"""
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
                "pe_ratio": getattr(metrics, 'pe_ratio', None),
                "pb_ratio": getattr(metrics, 'pb_ratio', None),
                "roe": getattr(metrics, 'roe', None),
                "dividend_yield": getattr(metrics, 'dividend_yield', None),
                "overall_score": getattr(metrics, 'overall_score', None),
                "recommendation": getattr(metrics, 'recommendation', ['N/A'])[0]
            }), 200
        else:
            return jsonify({"error": f"No data for {ticker}"}), 404
    except Exception as e:
        logger.error(f"Fundamental endpoint error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/admin/shutdown', methods=['POST'])
def shutdown():
    """Эндпоинт для graceful shutdown (только с API ключом)"""
    api_key = request.headers.get('X-Admin-Key')
    expected_key = os.getenv('ADMIN_KEY', '')

    if expected_key and api_key != expected_key:
        logger.warning(f"Unauthorized shutdown attempt from {request.remote_addr}")
        return jsonify({"error": "Unauthorized"}), 401

    logger.info("Shutdown requested via API")

    def shutdown_server():
        time.sleep(1)  # Даем время на ответ
        cleanup_resources()
        os._exit(0)

    import threading
    threading.Thread(target=shutdown_server, daemon=True).start()

    return jsonify({"message": "Server shutting down"}), 200


@app.route('/admin/restart', methods=['POST'])
def restart():
    """Эндпоинт для перезапуска бота (только с API ключом)"""
    api_key = request.headers.get('X-Admin-Key')
    expected_key = os.getenv('ADMIN_KEY', '')

    if expected_key and api_key != expected_key:
        logger.warning(f"Unauthorized restart attempt from {request.remote_addr}")
        return jsonify({"error": "Unauthorized"}), 401

    try:
        logger.info("Restarting trading bot...")
        if _trading_bot:
            _trading_bot.stop()
            time.sleep(2)
            _trading_bot.start()
            logger.info("Trading bot restarted successfully")
        return jsonify({"message": "Bot restarted successfully"}), 200
    except Exception as e:
        logger.error(f"Error restarting bot: {e}")
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

    # Для production используем waitress если доступен
    use_production_server = os.environ.get('USE_WAITRESS', 'false').lower() == 'true'

    print("\n" + "=" * 60)
    print(f"🚀 STARTING WEB SERVER")
    print(f"   Port: {port}")
    print(f"   Debug: {debug_mode}")
    print(f"   Production mode: {use_production_server}")
    print("=" * 60 + "\n")

    if use_production_server and not debug_mode:
        try:
            from waitress import serve

            print("📍 Using Waitress production server")
            serve(app, host='0.0.0.0', port=port, threads=4)
        except ImportError:
            print("⚠️ Waitress not installed, using Flask development server")
            print("   For production, install: pip install waitress")
            app.run(host='0.0.0.0', port=port, debug=debug_mode, threaded=False)
    else:
        app.run(host='0.0.0.0', port=port, debug=debug_mode, threaded=False)