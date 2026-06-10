# moex_sync_fetcher.py
"""Синхронный получатель свечей с MOEX (исправленная версия)"""

import requests
import time
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Optional

# Московское время (UTC+3)
MOSCOW_TZ = timezone(timedelta(hours=3))


class MoexSyncFetcher:
    """Синхронный клиент для MOEX ISS API (работает как в бэктестере)"""

    def __init__(self):
        self._last_request_time: Optional[datetime] = None
        self._min_interval = 0.2

    def _rate_limit(self):
        """Rate limiting для соблюдения лимитов API"""
        if self._last_request_time:
            elapsed = (datetime.now(MOSCOW_TZ) - self._last_request_time).total_seconds()
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
        self._last_request_time = datetime.now(MOSCOW_TZ)

    def get_candles(
            self,
            ticker: str,
            interval_minutes: int = 1,
            days: int = 30
    ) -> List[Tuple[float, float]]:
        """
        Получение свечей с MOEX

        Args:
            ticker: Тикер акции
            interval_minutes: Интервал в минутах (1, 5, 10, 15, 30, 60)
            days: Количество дней истории

        Returns:
            List[Tuple[float, float]]: Список кортежей (close, volume)
        """
        ticker = ticker.upper()

        # Маппинг интервалов MOEX
        interval_map = {
            1: 1,  # 1 минута
            5: 1,  # MOEX может не отдавать 5min, используем 1min
            10: 10,
            15: 15,
            30: 30,
            60: 60
        }
        moex_interval = interval_map.get(interval_minutes, 1)

        # Используем Московское время для корректных дат
        end_date = datetime.now(MOSCOW_TZ)
        start_date = end_date - timedelta(days=days)

        url = f"https://iss.moex.com/iss/engines/stock/markets/shares/boards/tqbr/securities/{ticker}/candles.json"
        params = {
            'interval': moex_interval,
            'from': start_date.strftime("%Y-%m-%d"),
            'till': end_date.strftime("%Y-%m-%d"),
            'start': 0
        }

        try:
            self._rate_limit()
            response = requests.get(url, params=params, timeout=30)

            if response.status_code != 200:
                return []  # тишина

            data = response.json()
            candles_data = data.get('candles', {})
            columns = candles_data.get('columns', [])
            rows = candles_data.get('data', [])

            if not columns or not rows:
                return []  # тишина

            try:
                close_idx = columns.index('close')
                volume_idx = columns.index('volume')
            except ValueError:
                return []  # тишина

            candles = []
            for row in rows:
                if len(row) > max(close_idx, volume_idx):
                    close = float(row[close_idx]) if row[close_idx] else 0
                    volume = float(row[volume_idx]) if row[volume_idx] else 0
                    if close > 0:
                        candles.append((close, volume))

            return candles

        except Exception:
            return []  # тишина


# Глобальный экземпляр
moex_sync = MoexSyncFetcher()