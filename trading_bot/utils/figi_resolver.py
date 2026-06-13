"""Поиск FIGI по тикеру и наоборот"""

from typing import Optional, Dict


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


class FigiResolver:
    """Разрешение FIGI и тикеров с кэшированием"""

    def __init__(self):
        self._ticker_to_figi: Dict[str, str] = {}
        self._figi_to_ticker: Dict[str, str] = {}
        self._shares_cache = None
        self._shares_cache_time = 0

    def _get_all_shares(self):
        """Получение всех акций с кэшированием"""
        import time
        now = time.time()
        if self._shares_cache is not None and (now - self._shares_cache_time) < 300:
            return self._shares_cache

        self._shares_cache = _get_tbank().get_all_shares(limit=1000)
        self._shares_cache_time = now
        return self._shares_cache

    def get_figi_by_ticker(self, ticker: str) -> Optional[str]:
        """Получение FIGI по тикеру"""
        ticker_upper = ticker.upper()
        if ticker_upper in self._ticker_to_figi:
            return self._ticker_to_figi[ticker_upper]

        try:
            all_shares = self._get_all_shares()
            for stock in all_shares:
                if stock.get('ticker') == ticker_upper:
                    figi = stock.get('figi')
                    self._ticker_to_figi[ticker_upper] = figi
                    self._figi_to_ticker[figi] = ticker_upper
                    return figi
        except Exception:
            pass

        return None

    def get_ticker_by_figi(self, figi: str) -> Optional[str]:
        """Получение тикера по FIGI"""
        if figi in self._figi_to_ticker:
            return self._figi_to_ticker[figi]

        try:
            all_shares = self._get_all_shares()
            for stock in all_shares:
                if stock.get('figi') == figi:
                    ticker = stock.get('ticker', '')
                    if ticker:
                        self._figi_to_ticker[figi] = ticker
                        self._ticker_to_figi[ticker] = figi
                        return ticker
        except Exception:
            pass

        return None