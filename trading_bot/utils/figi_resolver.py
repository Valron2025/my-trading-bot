# trading_bot/utils/figi_resolver.py

"""Resolver для преобразования тикеров в FIGI и обратно"""

from typing import Dict, Optional
from trading_bot.cache import TTLCache
from trading_bot.logger import debug, info, warning


class FigiResolver:
    """Resolver для преобразования тикеров в FIGI и обратно с кэшированием"""

    def __init__(self):
        self._ticker_to_figi: Dict[str, str] = {}
        self._figi_to_ticker: Dict[str, str] = {}
        self._loaded = False

        # Используем TTLCache для кэширования результатов
        self._figi_cache = TTLCache(default_ttl=3600, max_size=500, name="figi_resolver")

        # НЕ загружаем данные при инициализации (ленивая загрузка)
        # self._load_data()  # ← УБРАТЬ ЭТУ СТРОКУ!

    def _load_data(self):
        """Загрузка соответствий тикеров и FIGI"""
        try:
            # ✅ ИСПРАВЛЕНО: отложенный импорт ВНУТРИ метода
            from trading_bot.api.tbank_client import tbank

            info("🔄 Загрузка справочника инструментов...")

            all_shares = tbank.get_all_shares()
            if not all_shares:
                warning("⚠️ Не удалось загрузить список инструментов")
                return

            for share in all_shares:
                ticker = share.get('ticker')
                figi = share.get('figi')
                if ticker and figi:
                    self._ticker_to_figi[ticker.upper()] = figi
                    self._figi_to_ticker[figi] = ticker.upper()

            self._loaded = True
            info(f"✅ Загружено {len(self._ticker_to_figi)} инструментов")

        except Exception as e:
            warning(f"⚠️ Ошибка загрузки справочника: {e}")

    def get_figi_by_ticker(self, ticker: str) -> Optional[str]:
        """Получение FIGI по тикеру"""
        ticker_upper = ticker.upper()

        if not self._loaded:
            self._load_data()

        return self._ticker_to_figi.get(ticker_upper)

    def get_figi_by_ticker_cached(self, ticker: str, force_refresh: bool = False) -> Optional[str]:
        """Получение FIGI по тикеру с кэшированием"""
        ticker_upper = ticker.upper()

        if not force_refresh:
            cached = self._figi_cache.get(ticker_upper)
            if cached is not None:
                debug(f"📦 FigiResolver cache hit for {ticker_upper}")
                return cached

        figi = self.get_figi_by_ticker(ticker_upper)

        if figi:
            self._figi_cache.set(ticker_upper, figi, ttl=3600)
            debug(f"📦 FigiResolver cached {ticker_upper} -> {figi[:8]}...")

        return figi

    def get_ticker_by_figi(self, figi: str) -> Optional[str]:
        """Получение тикера по FIGI"""
        if figi in self._figi_to_ticker:
            return self._figi_to_ticker[figi]

        if not self._loaded:
            self._load_data()

        return self._figi_to_ticker.get(figi)

    def refresh(self):
        """Принудительное обновление справочника"""
        self._loaded = False
        self._ticker_to_figi.clear()
        self._figi_to_ticker.clear()
        self._figi_cache.clear()
        self._load_data()


# Глобальный экземпляр
_figi_resolver = None


def get_figi_resolver() -> FigiResolver:
    """Получение глобального экземпляра FigiResolver"""
    global _figi_resolver
    if _figi_resolver is None:
        _figi_resolver = FigiResolver()
    return _figi_resolver