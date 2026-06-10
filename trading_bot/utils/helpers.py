"""Вспомогательные функции и методы для бота"""

import time
from functools import wraps
from typing import List, Dict, Any, Optional

from ..logger import debug, info


# ========== УТИЛИТНЫЕ ФУНКЦИИ ==========

def safe_float(value, default: float = 0.0) -> float:
    """Безопасное преобразование в float"""
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def safe_divide(a, b, default: float = 0.0) -> float:
    """Безопасное деление с защитой от деления на ноль"""
    try:
        a_f, b_f = float(a), float(b)
        return a_f / b_f if b_f != 0 else default
    except (TypeError, ValueError):
        return default


def retry_on_fail(max_retries: int = 3, delay: float = 1):
    """Декоратор для повторных попыток при ошибках"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    from ..logger import warning
                    warning(f"⚠️ Ошибка {func.__name__}, попытка {attempt + 2}/{max_retries}: {e}")
                    time.sleep(delay * (attempt + 1))
            return None
        return wrapper
    return decorator


# ========== КЛАСС ВСПОМОГАТЕЛЬНЫХ МЕТОДОВ ==========

# class BotHelpers:
#     """Вспомогательные методы - кэширование, получение данных"""
#
#     def __init__(self, bot):
#         self.bot = bot
#
#     def _get_current_price(self, ticker: str) -> float:
#         """Получение текущей цены по тикеру"""
#         try:
#             figi = self.bot._get_figi_by_ticker(ticker)
#             if figi:
#                 from trading_bot.api.tbank_client import tbank
#                 return tbank.get_current_price(figi) or 0
#             return 0
#         except Exception as e:
#             debug(f"Ошибка получения цены для {ticker}: {e}")
#             return 0
#
#     def _get_positions(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
#         """Получение позиций с кэшированием"""
#         if not force_refresh:
#             cached = self.bot._positions_cache.get("all_positions")
#             if cached is not None:
#                 return cached
#         from trading_bot.api.tbank_client import tbank
#         positions = tbank.get_positions()
#         self.bot._positions_cache.set("all_positions", positions, ttl=5)
#         return positions
#
#     def _get_ticker_by_figi(self, figi: str) -> Optional[str]:
#         """Получение тикера по FIGI"""
#         return self.bot.figi_resolver.get_ticker_by_figi(figi)
#
#     def _get_figi_by_ticker(self, ticker: str) -> Optional[str]:
#         """Получение FIGI по тикеру"""
#         return self.bot.figi_resolver.get_figi_by_ticker(ticker)
#
#     def _is_blacklisted(self, ticker: str) -> bool:
#         """Проверка, в чёрном ли списке тикер"""
#         try:
#             from trading_bot.core.blacklist_manager import blacklist_manager
#             is_blocked, _ = blacklist_manager.is_blocked(ticker)
#             return is_blocked
#         except Exception:
#             return False
#
#     def _add_to_blacklist(self, ticker: str, minutes: int = 60):
#         """Добавление в чёрный список"""
#         try:
#             from trading_bot.core.blacklist_manager import blacklist_manager
#             blacklist_manager.add_temporary(ticker, ttl_minutes=minutes)
#             info(f"⛔ {ticker} добавлен в чёрный список на {minutes} минут")
#         except Exception as e:
#             debug(f"Ошибка добавления в чёрный список: {e}")
#
#     def _track_smart_order(self, order_id: Optional[str], ticker: str, quantity: int, order_type: str):
#         """Отслеживание умных заявок"""
#         if not hasattr(self.bot, '_smart_orders_tracking'):
#             self.bot._smart_orders_tracking = []
#         self.bot._smart_orders_tracking.append({
#             'order_id': order_id,
#             'ticker': ticker,
#             'quantity': quantity,
#             'type': order_type,
#             'time': datetime.now()
#         })
#         if len(self.bot._smart_orders_tracking) > 100:
#             self.bot._smart_orders_tracking = self.bot._smart_orders_tracking[-100:]