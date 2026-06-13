"""Клиент для получения свечей через официальный пакет t-tech-investments"""

from datetime import datetime
from typing import List, Tuple, Optional

from ..logger import success, warning, debug


class TInvestCandlesClient:
    """Клиент для получения свечей через официальный T-Invest API"""

    def __init__(self, token: str):
        self.token = token
        self._client = None

    def _get_client(self):
        """Ленивое создание клиента"""
        if self._client is None:
            try:
                from t_tech.invest import Client
                self._client = Client(self.token)
            except ImportError:
                warning("⚠️ Библиотека t_tech.invest не установлена")
                return None
        return self._client

    def get_figi_by_ticker(self, ticker: str) -> Optional[str]:
        """Получение FIGI по тикеру через глобальный клиент"""
        try:
            from ..api.tbank_client import tbank
            all_shares = tbank.get_all_shares(limit=1000)

            for stock in all_shares:
                if stock.get('ticker', '').upper() == ticker.upper():
                    figi = stock.get('figi')
                    debug(f"🔍 Найден FIGI для {ticker}: {figi}")
                    return figi

            warning(f"⚠️ Тикер {ticker} не найден в списке акций")
            return None
        except Exception as e:
            debug(f"Ошибка получения FIGI для {ticker}: {e}")
            return None

    def get_candles(
        self,
        figi: str,
        interval_minutes: int = 5,
        days: int = 5,
        limit: int = 500
    ) -> List[Tuple[float, float]]:
        """Получение свечей через T-Invest API (делегирует tbank)"""
        if not figi:
            warning("⚠️ FIGI не указан")
            return []

        try:
            from ..api.tbank_client import tbank
            candles = tbank.get_candles(figi, days=days, interval_minutes=interval_minutes)

            if candles:
                if limit and len(candles) > limit:
                    candles = candles[:limit]
                # Возвращаем только (close, volume) для совместимости
                result = [(c[0], c[1]) for c in candles]
                success(f"✅ T-Invest API: {len(result)} свечей для {figi}")
                return result
            else:
                warning(f"⚠️ Нет данных для {figi}")
                return []

        except Exception as e:
            warning(f"❌ Ошибка T-Invest API для {figi}: {e}")
            return []

    def get_current_price(self, figi: str) -> Optional[float]:
        """Получение текущей цены (делегирует tbank)"""
        try:
            from ..api.tbank_client import tbank
            return tbank.get_current_price(figi)
        except Exception as e:
            debug(f"Ошибка получения цены для {figi}: {e}")
            return None


# Глобальный экземпляр
_candles_client = None


def get_candles_client():
    """Получение глобального клиента для свечей"""
    global _candles_client
    if _candles_client is None:
        from ..config import config
        _candles_client = TInvestCandlesClient(config.tbank_token)
    return _candles_client