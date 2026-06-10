"""Проверка состояния рынка"""

from datetime import datetime, time as dt_time

from ..utils.time_utils import get_moscow_time


class MarketChecker:
    """Проверка состояния рынка и режимов торгов"""

    def __init__(self):
        self._otc_mode_cache = None
        self._otc_cache_time = 0

    def is_otc_mode(self) -> bool:
        """Проверка OTC режима (внебиржевая торговля)"""
        now = get_moscow_time()
        current_time = now.time()
        weekday = now.weekday()

        # В выходные OTC режим
        if weekday >= 5:
            return True

        MORNING_START = dt_time(6, 50)
        MORNING_END = dt_time(9, 50)
        MAIN_START = dt_time(9, 50)
        MAIN_END = dt_time(18, 59)
        EVENING_START = dt_time(19, 0)

        # Основная сессия — не OTC
        if MAIN_START <= current_time <= MAIN_END:
            return False

        # Утренняя сессия — не OTC
        if MORNING_START <= current_time <= MORNING_END:
            return False

        # Вечерняя сессия — OTC
        if current_time >= EVENING_START:
            return True

        # Ночь — не OTC (биржа закрыта)
        return False

    def is_main_session(self) -> bool:
        """Проверка основной сессии"""
        now = get_moscow_time()
        current_time = now.time()
        weekday = now.weekday()

        if weekday >= 5:  # выходные
            return False

        MAIN_START = dt_time(9, 50)
        MAIN_END = dt_time(18, 59)

        return MAIN_START <= current_time <= MAIN_END

    def is_evening_session(self) -> bool:
        """Проверка вечерней сессии"""
        now = get_moscow_time()
        current_time = now.time()
        weekday = now.weekday()

        if weekday >= 5:
            return False

        EVENING_START = dt_time(19, 0, 1)
        EVENING_END = dt_time(23, 49, 59)

        return EVENING_START <= current_time <= EVENING_END

    def is_morning_session(self) -> bool:
        """Проверка утренней сессии"""
        now = get_moscow_time()
        current_time = now.time()
        weekday = now.weekday()

        if weekday >= 5:
            return False

        MORNING_START = dt_time(6, 50)
        MORNING_END = dt_time(9, 49, 59)

        return MORNING_START <= current_time <= MORNING_END

    def get_session_name(self) -> str:
        """Получение названия текущей сессии"""
        if self.is_morning_session():
            return "morning"
        elif self.is_main_session():
            return "main"
        elif self.is_evening_session():
            return "evening"
        else:
            return "closed"


# Глобальный экземпляр
market_checker = MarketChecker()