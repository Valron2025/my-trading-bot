"""Управление торговыми сессиями"""

import threading
import time
from datetime import datetime, time as dt_time
from typing import Optional

from ..logger import info, success, error, debug
from ..utils.time_utils import get_moscow_time


class SessionManager:
    """Менеджер торговых сессий - отслеживает время и управляет состоянием"""

    def __init__(self, bot):
        self.bot = bot
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._current_session = "unknown"

        # Время сессий (МСК)
        self.MORNING_START = dt_time(6, 50)
        self.MORNING_END = dt_time(9, 49, 59)

        self.MAIN_START = dt_time(9, 50)
        self.MAIN_END = dt_time(18, 59)

        self.EVENING_START = dt_time(19, 0, 1)
        self.EVENING_END = dt_time(23, 49, 59)

        self.AUCTION_START = dt_time(18, 55)
        self.AUCTION_END = dt_time(18, 59, 30)

        # Выходные
        self.WEEKEND_START = dt_time(10, 0)
        self.WEEKEND_END = dt_time(18, 59)

    def start(self, trading_bot=None):
        """Запуск менеджера сессий"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="SessionManager")
        self._thread.start()
        success("⏰ SessionManager запущен")

    def stop(self):
        """Остановка менеджера сессий"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        success("⏰ SessionManager остановлен")

    def _run(self):
        """Основной цикл"""
        while self._running:
            try:
                now = get_moscow_time()
                current_time = now.time()
                weekday = now.weekday()
                is_weekend = weekday >= 5

                # Определяем текущую сессию
                if is_weekend:
                    if self.WEEKEND_START <= current_time <= self.WEEKEND_END:
                        new_session = "weekend"
                    else:
                        new_session = "closed"
                elif self.AUCTION_START <= current_time <= self.AUCTION_END:
                    new_session = "auction"
                elif self.MORNING_START <= current_time <= self.MORNING_END:
                    new_session = "morning"
                elif self.MAIN_START <= current_time <= self.MAIN_END:
                    new_session = "main"
                elif self.EVENING_START <= current_time <= self.EVENING_END:
                    new_session = "evening"
                else:
                    new_session = "closed"

                # Логируем смену сессии
                if new_session != self._current_session:
                    self._on_session_change(self._current_session, new_session)
                    self._current_session = new_session

                time.sleep(30)

            except Exception as e:
                debug(f"SessionManager error: {e}")
                time.sleep(60)

    def _on_session_change(self, old_session: str, new_session: str):
        """Обработка смены сессии"""
        info(f"\n{'=' * 60}")
        info(f"🕐 СМЕНА СЕССИИ: {old_session} → {new_session}")
        info(f"{'=' * 60}")

        # Отправляем уведомление в Telegram
        try:
            from trading_bot.telegram.telegram_notifier import get_telegram_notifier
            telegram = get_telegram_notifier()
            if telegram and telegram.enabled:
                session_names = {
                    "morning": "🌅 УТРЕННЯЯ СЕССИЯ (6:50-9:50)",
                    "main": "📈 ОСНОВНАЯ СЕССИЯ (9:50-18:59)",
                    "evening": "🌙 ВЕЧЕРНЯЯ СЕССИЯ (19:00-23:49)",
                    "auction": "🏛️ АУКЦИОН ЗАКРЫТИЯ (18:55-18:59)",
                    "weekend": "📊 ВЫХОДНЫЕ (10:00-18:59)",
                    "closed": "🔒 РЫНОК ЗАКРЫТ"
                }
                message = session_names.get(new_session, f"Сессия: {new_session}")
                telegram.send_info(message)
        except Exception:
            pass

    def is_trading_allowed(self, ticker: str = None) -> bool:
        """Проверка, можно ли торговать сейчас"""
        now = get_moscow_time()
        current_time = now.time()
        weekday = now.weekday()
        is_weekend = weekday >= 5

        if is_weekend:
            return self.WEEKEND_START <= current_time <= self.WEEKEND_END

        # Основные сессии
        if self.MORNING_START <= current_time <= self.MORNING_END:
            return True
        if self.MAIN_START <= current_time <= self.MAIN_END:
            return True
        if self.EVENING_START <= current_time <= self.EVENING_END:
            # Проверяем, торгуется ли ticker в вечернюю сессию
            if ticker:
                evening_tickers = self._get_evening_session_tickers()
                if ticker.upper() not in evening_tickers:
                    return False
            return True

        return False

    def _get_evening_session_tickers(self) -> set:
        """Возвращает множество тикеров для вечерней сессии"""
        return {
            'SBER', 'SBERP', 'VTBR', 'GAZP', 'LKOH', 'ROSN', 'TATN', 'TATNP',
            'NVTK', 'SNGS', 'SNGSP', 'MGNT', 'MTSS', 'CHMF', 'NLMK', 'GMKN',
            'PLZL', 'POLY', 'YNDX', 'TCSG', 'OZON', 'FIXP', 'PIKK', 'MAGN',
            'RUAL', 'AFLT', 'URKA', 'MOEX', 'POSI', 'SIBN', 'AFKS', 'HYDR',
            'PHOR', 'FIVE', 'TRNFP', 'APTK', 'ENPG', 'RSTI', 'IRAO', 'FEES',
        }

    def get_current_session(self) -> str:
        """Возвращает текущую сессию"""
        return self._current_session

    def get_minutes_to_end(self) -> tuple:
        """Возвращает минут до конца сессии и тип сессии"""
        now = get_moscow_time()
        current_time = now.time()

        if self.MAIN_START <= current_time <= self.MAIN_END:
            end_time = now.replace(hour=18, minute=59, second=0, microsecond=0)
            minutes_left = max(0, (end_time - now).total_seconds() / 60)
            return minutes_left, "main"
        elif self.EVENING_START <= current_time <= self.EVENING_END:
            end_time = now.replace(hour=23, minute=49, second=0, microsecond=0)
            minutes_left = max(0, (end_time - now).total_seconds() / 60)
            return minutes_left, "evening"

        return 0, "closed"


# Глобальный экземпляр
session_manager = SessionManager(None)