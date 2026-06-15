"""Trading Bot Package - Автоматическая торговля на T-Investments"""

import sys
import os
import logging

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .bot import TradingBot
from .config import config  # ✅ теперь config в той же папке
from .models import (
    StockCandidate,
    OrderSide,
    Position,
    StockAnalysis,
    calculate_pnl,
    calculate_pnl_with_commission
)
from .cache import TTLCache, price_cache, positions_cache, candles_cache

# Настройка логгера пакета
logger = logging.getLogger(__name__)

# Глобальный экземпляр бота (для обратной совместимости)
_trading_bot_instance = None


# ========== ИНИЦИАЛИЗАЦИЯ МОНИТОРИНГА ==========
def init_monitoring_system(bot=None):
    """Инициализация системы мониторинга"""
    from trading_bot.monitoring import init_monitoring, get_memory_monitor

    result = init_monitoring(bot=bot, prometheus_port=8001, watchdog_timeout=300)

    memory_monitor = get_memory_monitor()
    if memory_monitor:
        memory_monitor.log_usage()

    return result


# В функцию get_trading_bot() или init_trading_bot() добавьте:
def get_trading_bot():
    global _trading_bot_instance
    if _trading_bot_instance is None:
        from .bot import TradingBot
        _trading_bot_instance = TradingBot()
        # ✅ ИНИЦИАЛИЗИРУЕМ МОНИТОРИНГ
        init_monitoring_system(_trading_bot_instance)
        logger.info("✅ Глобальный экземпляр TradingBot создан")
    return _trading_bot_instance


def init_trading_bot():
    """Инициализация глобального экземпляра бота"""
    global _trading_bot_instance
    from .bot import TradingBot
    _trading_bot_instance = TradingBot()
    logger.info("✅ TradingBot инициализирован через init_trading_bot()")
    return _trading_bot_instance


def reset_trading_bot():
    """Сброс глобального экземпляра бота (для тестов)"""
    global _trading_bot_instance
    _trading_bot_instance = None
    logger.info("🔄 Глобальный экземпляр TradingBot сброшен")


def is_bot_running() -> bool:
    """Проверка, запущен ли бот"""
    if _trading_bot_instance is None:
        return False
    return getattr(_trading_bot_instance, '_running', False)


__version__ = "3.0.0"

# Единый список экспортов
__all__ = [
    # Основные классы и функции
    "TradingBot",
    "get_trading_bot",
    "init_trading_bot",
    "reset_trading_bot",
    "is_bot_running",
    "config",

    # Модели данных
    "StockCandidate",
    "OrderSide",
    "Position",
    "StockAnalysis",

    # Функции расчёта
    "calculate_pnl",
    "calculate_pnl_with_commission",

    # Кэширование
    "TTLCache",
    "price_cache",
    "positions_cache",
    "candles_cache",
]

# Логируем успешную инициализацию пакета
logger.info(f"📦 Trading Bot Package v{__version__} initialized")