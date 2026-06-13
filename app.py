"""Flask сервер для health check и вебхука Telegram"""

import os
import sys
import threading
import logging
import sqlite3
from flask import Flask, jsonify, request
from datetime import datetime, timezone, timedelta

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MOSCOW_TZ = timezone(timedelta(hours=3))

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Флаг, что бот уже запущен через run.py
BOT_RUNNING = os.environ.get('BOT_ALREADY_STARTED', 'false').lower() == 'true'

# Статус бота из глобальной переменной (устанавливается в run.py)
_trading_bot_status = {
    'running': False,
    'cycle_count': 0,
    'positions': 0,
    'capital': 0
}

# Глобальная ссылка на Telegram бота
_telegram_bot = None


def now_msk():
    """Возвращает текущее московское время в ISO формате"""
    return datetime.now(MOSCOW_TZ).isoformat()


def update_bot_status(running: bool = None, cycle_count: int = None,
                      positions: int = None, capital: float = None):
    """Обновление статуса бота (вызывается из run.py)"""
    global _trading_bot_status
    if running is not None:
        _trading_bot_status['running'] = running
    if cycle_count is not None:
        _trading_bot_status['cycle_count'] = cycle_count
    if positions is not None:
        _trading_bot_status['positions'] = positions
    if capital is not None:
        _trading_bot_status['capital'] = capital


def set_telegram_bot(bot):
    """Установка глобальной ссылки на Telegram бота"""
    global _telegram_bot
    _telegram_bot = bot


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ФУНДАМЕНТАЛЬНЫХ ЭНДПОИНТОВ ==========

def _get_fundamental_bot():
    """Получение бота с проверкой фундаментальных модулей"""
    try:
        from trading_bot import get_trading_bot
        bot = get_trading_bot()

        if hasattr(bot, 'fundamental_db') and bot.fundamental_db:
            return bot
        return None
    except Exception as e:
        logger.error(f"Ошибка получения бота: {e}")
        return None


def _get_watchlist(bot=None):
    """Динамическое получение списка отслеживаемых тикеров"""
    if bot is None:
        bot = _get_fundamental_bot()

    watchlist = []

    # 1. Получаем тикеры из current_multipliers (активные)
    if bot and hasattr(bot, 'fundamental_db'):
        try:
            multipliers = bot.fundamental_db.get_all_current_multipliers()
            watchlist.extend(multipliers.keys())
        except Exception as e:
            logger.debug(f"Ошибка получения multipliers: {e}")

    # 2. Получаем тикеры из истории (если есть)
    if bot and hasattr(bot, 'fundamental_db'):
        try:
            with sqlite3.connect(bot.fundamental_db.db_path) as conn:
                cursor = conn.execute("""
                    SELECT DISTINCT ticker FROM fundamental_metrics_history 
                    ORDER BY ticker
                """)
                history_tickers = [row[0] for row in cursor.fetchall()]
                for ticker in history_tickers:
                    if ticker not in watchlist:
                        watchlist.append(ticker)
        except Exception as e:
            logger.debug(f"Ошибка получения истории тикеров: {e}")

    # 3. Если список пуст - используем дефолтный список
    if not watchlist:
        watchlist = ["SBER", "GAZP", "LKOH", "ROSN", "TATN", "NVTK", "MGNT"]
        logger.debug(f"Используем дефолтный watchlist: {watchlist}")

    # Сортируем для консистентности
    watchlist.sort()

    return watchlist


def _get_ticker_sector(ticker: str) -> str:
    """Определение сектора тикера (для дополнительной информации)"""
    sectors = {
        'SBER': 'bank', 'SBERP': 'bank',
        'VTBR': 'bank',
        'GAZP': 'gas', 'NVTK': 'gas',
        'LKOH': 'oil', 'ROSN': 'oil', 'TATN': 'oil', 'TATNP': 'oil',
        'MGNT': 'retail', 'MAGN': 'retail',
        'MTSS': 'telecom', 'RTKM': 'telecom'
    }
    return sectors.get(ticker.upper(), 'other')


# ========== ОСНОВНЫЕ ЭНДПОИНТЫ ==========

@app.route('/')
@app.route('/health')
def health_check():
    """Health check endpoint для Render"""
    return jsonify({
        "status": "running",
        "timestamp": datetime.now().isoformat(),
        "bot_started": BOT_RUNNING,
        "bot_running": _trading_bot_status.get('running', False)
    }), 200


@app.route('/status')
def bot_status():
    """Получение статуса бота"""
    try:
        from trading_bot.api.tbank_client import tbank
        from trading_bot import get_trading_bot

        trading_bot = get_trading_bot()
        available, total, _ = tbank.get_available_funds()

        positions = []
        if hasattr(trading_bot, '_get_positions'):
            positions = trading_bot._get_positions()

        return jsonify({
            "running": getattr(trading_bot, '_running', False),
            "capital": total,
            "available": available,
            "positions": len(positions),
            "cycle_count": getattr(trading_bot, '_cycle_count', 0),
            "timestamp": datetime.now().isoformat()
        }), 200

    except Exception as e:
        logger.warning(f"Ошибка получения статуса из бота: {e}")
        return jsonify({
            "running": _trading_bot_status.get('running', False),
            "capital": _trading_bot_status.get('capital', 0),
            "positions": _trading_bot_status.get('positions', 0),
            "cycle_count": _trading_bot_status.get('cycle_count', 0),
            "timestamp": datetime.now().isoformat(),
            "error": str(e) if os.environ.get('DEBUG') else None
        }), 200


@app.route('/health/detailed')
def health_detailed():
    """Детальный health check для диагностики"""
    import sys

    modules_status = {
        "trading_bot": False,
        "tbank_client": False,
        "telegram_notifier": False,
        "position_manager": False
    }

    try:
        from trading_bot import get_trading_bot
        get_trading_bot()
        modules_status["trading_bot"] = True
    except ImportError as e:
        modules_status["trading_bot"] = str(e)

    try:
        from trading_bot.api.tbank_client import tbank
        modules_status["tbank_client"] = True
    except ImportError as e:
        modules_status["tbank_client"] = str(e)

    try:
        from trading_bot.telegram.telegram_notifier import get_telegram_notifier
        modules_status["telegram_notifier"] = True
    except ImportError as e:
        modules_status["telegram_notifier"] = str(e)

    try:
        from trading_bot.risk.position_manager import position_manager
        modules_status["position_manager"] = True
    except ImportError as e:
        modules_status["position_manager"] = str(e)

    return jsonify({
        "status": "running",
        "python_version": sys.version,
        "environment": {
            "bot_already_started": BOT_RUNNING,
            "tbank_token_set": bool(os.environ.get('TBANK_TOKEN')),
            "telegram_token_set": bool(os.environ.get('TELEGRAM_TOKEN')),
            "telegram_chat_id_set": bool(os.environ.get('TELEGRAM_CHAT_ID')),
            "port": os.environ.get('PORT', '5000')
        },
        "bot_status": _trading_bot_status,
        "modules": modules_status,
        "timestamp": datetime.now().isoformat()
    }), 200


@app.route('/internal/update_status', methods=['POST'])
def internal_update_status():
    """Внутренний эндпоинт для обновления статуса бота"""
    try:
        data = request.get_json()
        if data:
            update_bot_status(
                running=data.get('running'),
                cycle_count=data.get('cycle_count'),
                positions=data.get('positions'),
                capital=data.get('capital')
            )
            logger.info(f"Статус бота обновлён: running={data.get('running')}")
        return jsonify({"success": True}), 200
    except Exception as e:
        logger.error(f"Ошибка обновления статуса: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/validation/status')
def validation_status():
    """Получение статуса валидации"""
    try:
        from trading_bot import get_trading_bot
        trading_bot = get_trading_bot()
        if trading_bot and hasattr(trading_bot, 'get_validation_stats'):
            stats = trading_bot.get_validation_stats()
            return jsonify({
                "status": "ok",
                "validation_cache": stats,
                "cache_size": len(stats),
                "timestamp": datetime.now().isoformat()
            }), 200
        return jsonify({
            "status": "error",
            "error": "Validation stats not available"
        }), 503
    except Exception as e:
        logger.error(f"Ошибка получения статуса валидации: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route('/validation/clear', methods=['POST'])
def validation_clear():
    """Очистка кэша валидации"""
    try:
        from trading_bot import get_trading_bot
        trading_bot = get_trading_bot()
        if trading_bot and hasattr(trading_bot, 'clear_validation_cache'):
            trading_bot.clear_validation_cache()
            logger.info("Кэш валидации очищен через API")
            return jsonify({"status": "success", "message": "Cache cleared"}), 200
        return jsonify({"status": "error", "error": "Clear validation not available"}), 503
    except Exception as e:
        logger.error(f"Ошибка очистки кэша валидации: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route('/moex/status')
def moex_status():
    """Статус MOEX клиента"""
    try:
        from trading_bot.core.moex_client import moex_client
        return jsonify({"available": True, "status": "ok"}), 200
    except ImportError:
        return jsonify({"available": False, "status": "not_available"}), 503
    except Exception as e:
        logger.error(f"Ошибка MOEX клиента: {e}")
        return jsonify({"available": False, "status": "error", "error": str(e)}), 503


@app.route('/ping')
def ping():
    """Простой ping для Keep-Alive"""
    return "pong", 200


@app.route('/metrics')
def metrics():
    """Метрики для мониторинга"""
    return jsonify({
        "bot_running": 1 if _trading_bot_status.get('running') else 0,
        "bot_cycle_count": _trading_bot_status.get('cycle_count', 0),
        "bot_positions": _trading_bot_status.get('positions', 0),
        "bot_capital": _trading_bot_status.get('capital', 0),
        "timestamp": datetime.now().isoformat()
    }), 200


@app.route('/health/ready')
def readiness_check():
    """Проверка готовности приложения"""
    try:
        from trading_bot.api.tbank_client import tbank
        available, total, _ = tbank.get_available_funds()
        if total > 0:
            return jsonify({"status": "ready"}), 200
        return jsonify({"status": "not_ready", "reason": "Cannot get balance"}), 503
    except Exception as e:
        return jsonify({"status": "not_ready", "reason": str(e)}), 503


@app.route('/health/liveness')
def liveness_check():
    """Проверка живости приложения"""
    return jsonify({"status": "alive"}), 200


# ========== ФУНДАМЕНТАЛЬНЫЕ ЭНДПОИНТЫ ==========

@app.route('/fundamental/status')
def fundamental_status():
    """Статус фундаментального анализатора"""
    bot = _get_fundamental_bot()
    if not bot or not hasattr(bot, 'fundamental_updater'):
        return jsonify({"status": "not_available"}), 503

    stats = bot.fundamental_updater.stats
    watchlist = _get_watchlist(bot)

    return jsonify({
        "status": "running",
        "last_update": stats.get('last_update'),
        "total_updates": stats.get('total_updates', 0),
        "successful_updates": stats.get('successful_updates', 0),
        "failed_updates": stats.get('failed_updates', 0),
        "watchlist_size": len(watchlist),
        "watchlist": watchlist,
        "timestamp": datetime.now().isoformat()
    }), 200


@app.route('/fundamental/multipliers')
def fundamental_multipliers():
    """Получение всех текущих мультипликаторов"""
    bot = _get_fundamental_bot()
    if not bot:
        return jsonify({"status": "not_initialized"}), 200

    multipliers = bot.fundamental_db.get_all_current_multipliers()

    # Добавляем сектор для каждого тикера
    for ticker, data in multipliers.items():
        data['sector'] = _get_ticker_sector(ticker)

    return jsonify({
        "status": "ok",
        "count": len(multipliers),
        "multipliers": multipliers,
        "timestamp": datetime.now().isoformat()
    }), 200


@app.route('/fundamental/<ticker>')
def fundamental_ticker(ticker: str):
    """Получение фундаментальных данных по тикеру"""
    bot = _get_fundamental_bot()
    if not bot:
        return jsonify({"status": "not_initialized"}), 200

    ticker = ticker.upper()
    current = bot.fundamental_db.get_current_multipliers(ticker)
    history = bot.fundamental_db.get_history(ticker, days=30)
    trend = bot.fundamental_db.get_trend(ticker)

    return jsonify({
        "ticker": ticker,
        "sector": _get_ticker_sector(ticker),
        "current": current,
        "history": history[:10],
        "trend": trend,
        "timestamp": datetime.now().isoformat()
    }), 200


@app.route('/fundamental/history/<ticker>')
def fundamental_history(ticker: str):
    """Получение полной истории фундаментальных показателей по тикеру"""
    bot = _get_fundamental_bot()
    if not bot:
        return jsonify({"status": "not_initialized"}), 200

    ticker = ticker.upper()
    days = request.args.get('days', 30, type=int)
    history = bot.fundamental_db.get_history(ticker, days=days)

    return jsonify({
        "ticker": ticker,
        "days": days,
        "records": len(history),
        "history": history,
        "timestamp": datetime.now().isoformat()
    }), 200


@app.route('/fundamental/trends')
def fundamental_trends():
    """Получение трендов фундаментальных показателей для всех тикеров"""
    bot = _get_fundamental_bot()
    if not bot:
        return jsonify({"status": "not_initialized"}), 200

    watchlist = _get_watchlist(bot)
    trends = {}

    for ticker in watchlist:
        trend = bot.fundamental_db.get_trend(ticker)
        trends[ticker] = {
            'trend': trend.get('trend'),
            'change_pct': trend.get('change_pct', 0),
            'current_score': trend.get('current'),
            'previous_score': trend.get('previous'),
            'sector': _get_ticker_sector(ticker)
        }

    return jsonify({
        "status": "ok",
        "watchlist_size": len(watchlist),
        "watchlist": watchlist,
        "trends": trends,
        "timestamp": datetime.now().isoformat()
    }), 200


@app.route('/fundamental/watchlist')
def fundamental_watchlist():
    """Получение динамического списка отслеживаемых тикеров"""
    bot = _get_fundamental_bot()
    watchlist = _get_watchlist(bot)

    # Получаем дополнительную информацию по каждому тикеру
    detailed_watchlist = []
    for ticker in watchlist:
        ticker_info = {
            'ticker': ticker,
            'sector': _get_ticker_sector(ticker)
        }

        # Добавляем текущие мультипликаторы, если есть
        if bot and hasattr(bot, 'fundamental_db'):
            current = bot.fundamental_db.get_current_multipliers(ticker)
            if current:
                ticker_info['pe_ratio'] = current.get('pe_ratio')
                ticker_info['roe'] = current.get('roe')
                ticker_info['dividend_yield'] = current.get('dividend_yield')

        detailed_watchlist.append(ticker_info)

    return jsonify({
        "status": "ok",
        "source": "dynamic",
        "count": len(detailed_watchlist),
        "watchlist": detailed_watchlist,
        "timestamp": datetime.now().isoformat()
    }), 200


@app.route('/fundamental/watchlist/add', methods=['POST'])
def fundamental_watchlist_add():
    """Добавление тикера в watchlist (принудительное обновление)"""
    try:
        data = request.get_json()
        ticker = data.get('ticker', '').upper()

        if not ticker:
            return jsonify({"status": "error", "error": "ticker required"}), 400

        bot = _get_fundamental_bot()
        if not bot:
            return jsonify({"status": "not_initialized"}), 200

        # Принудительно обновляем данные по тикеру
        import asyncio

        def update_single():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(bot.fundamental_updater.update_ticker(ticker))
                logger.info(f"Тикер {ticker} добавлен в watchlist и обновлён")
            except Exception as e:
                logger.error(f"Ошибка добавления {ticker}: {e}")
            finally:
                loop.close()

        thread = threading.Thread(target=update_single, daemon=True)
        thread.start()

        return jsonify({
            "status": "started",
            "message": f"Тикер {ticker} добавляется в систему",
            "timestamp": datetime.now().isoformat()
        }), 202

    except Exception as e:
        logger.error(f"Ошибка добавления тикера: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route('/fundamental/watchlist/clear', methods=['POST'])
def fundamental_watchlist_clear():
    """Очистка старых записей из watchlist (оставляем только активные)"""
    bot = _get_fundamental_bot()
    if not bot:
        return jsonify({"status": "not_initialized"}), 200

    try:
        with sqlite3.connect(bot.fundamental_db.db_path) as conn:
            # Удаляем записи старше 90 дней
            cursor = conn.execute("""
                DELETE FROM fundamental_metrics_history 
                WHERE fetched_date < date('now', '-90 days')
            """)
            deleted = cursor.rowcount

        logger.info(f"Очищено {deleted} старых записей")

        return jsonify({
            "status": "ok",
            "deleted_records": deleted,
            "message": f"Удалено {deleted} старых записей",
            "timestamp": datetime.now().isoformat()
        }), 200

    except Exception as e:
        logger.error(f"Ошибка очистки: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route('/fundamental/summary')
def fundamental_summary():
    """Сводка по фундаментальным данным"""
    bot = _get_fundamental_bot()
    if not bot:
        return jsonify({"status": "not_initialized"}), 200

    summary = bot.fundamental_db.get_summary()
    watchlist = _get_watchlist(bot)

    # Добавляем статистику по секторам
    sectors = {}
    for ticker in watchlist:
        sector = _get_ticker_sector(ticker)
        sectors[sector] = sectors.get(sector, 0) + 1

    return jsonify({
        "status": "ok",
        "summary": summary,
        "watchlist_size": len(watchlist),
        "sectors": sectors,
        "timestamp": datetime.now().isoformat()
    }), 200


@app.route('/fundamental/update', methods=['POST'])
def fundamental_update():
    """Ручное обновление фундаментальных данных"""
    bot = _get_fundamental_bot()
    if not bot or not hasattr(bot, 'fundamental_updater'):
        return jsonify({"status": "not_available"}), 503

    import asyncio

    def run_update():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(bot.fundamental_updater.update_all())
            logger.info("Ручное обновление фундаментальных данных завершено")
        except Exception as e:
            logger.error(f"Ошибка ручного обновления: {e}")
        finally:
            loop.close()

    thread = threading.Thread(target=run_update, daemon=True)
    thread.start()

    return jsonify({
        "status": "started",
        "message": "Фундаментальные данные обновляются в фоне",
        "timestamp": datetime.now().isoformat()
    }), 202


# ========== ОБРАБОТЧИКИ ОШИБОК ==========

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({"error": "Internal server error"}), 500


def init_app():
    """Инициализация приложения - вызывается из run.py"""
    pass


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)