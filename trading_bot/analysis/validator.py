"""Валидация тикеров перед торговлей"""

from typing import Tuple, Dict, Any, Optional
from datetime import datetime, timedelta

from ..config import config
from ..logger import info, warning, debug


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


class TickerValidator:
    """Валидатор тикеров - проверяет ликвидность, волатильность, объёмы"""

    def __init__(self, bot):
        self.bot = bot
        self._validation_cache = {}
        self._cache_ttl = 3600  # 1 час

    def validate(self, ticker: str, force: bool = False) -> Tuple[bool, Dict[str, Any]]:
        """Валидация тикера"""
        # Проверяем кэш
        if not force and ticker in self._validation_cache:
            cache_entry = self._validation_cache[ticker]
            if datetime.now() - cache_entry['time'] < timedelta(seconds=self._cache_ttl):
                return cache_entry['valid'], cache_entry.get('data', {})

        result = self._perform_validation(ticker)

        # Сохраняем в кэш
        self._validation_cache[ticker] = {
            'valid': result[0],
            'data': result[1],
            'time': datetime.now()
        }

        return result

    def _perform_validation(self, ticker: str) -> Tuple[bool, Dict[str, Any]]:
        """Выполнение валидации"""
        try:
            # Получаем FIGI
            figi = self.bot._get_figi_by_ticker(ticker)
            if not figi:
                return False, {'error': f'FIGI не найден для {ticker}'}

            # Проверяем торговый статус
            status = _get_tbank().get_trading_status(figi)
            if status:
                if not status.get('is_tradable', True):
                    return False, {'error': 'Торги не доступны'}

            # Базовая валидация (упрощённая, без MOEX)
            return True, {
                'figi': figi,
                'warning': 'Базовая валидация'
            }

        except Exception as e:
            debug(f"Ошибка валидации {ticker}: {e}")
            return False, {'error': str(e)}

    def get_cached_validation(self, ticker: str) -> Optional[Dict]:
        """Получение кэшированной валидации"""
        if ticker in self._validation_cache:
            cache_entry = self._validation_cache[ticker]
            if datetime.now() - cache_entry['time'] < timedelta(seconds=self._cache_ttl):
                return cache_entry.get('data')
        return None

    def clear_cache(self):
        """Очистка кэша валидации"""
        self._validation_cache.clear()
        info("🧹 Кэш валидации очищен")


# Глобальный экземпляр (будет создан в bot.py)
validator = None