"""Управление торговыми сессиями"""

import threading
import time
from datetime import datetime, time as dt_time
from typing import Optional, Tuple

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

        # Кэш для статуса торгов
        self._trading_status_cache = {}
        self._cache_ttl = 60  # 60 секунд

    def start_session(self, trading_bot=None):
        """Запуск менеджера сессий"""
        if trading_bot:
            self.bot = trading_bot
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        info("⏰ SessionManager запущен")

    def stop_session(self):
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
        """
        Проверка, можно ли торговать сейчас
        ✅ ИСПРАВЛЕНО: удалён жёсткий список тикеров для вечерней сессии
        """
        now = get_moscow_time()
        current_time = now.time()
        weekday = now.weekday()
        is_weekend = weekday >= 5

        # Выходные
        if is_weekend:
            return self.WEEKEND_START <= current_time <= self.WEEKEND_END

        # Утренняя сессия (pre-market)
        if self.MORNING_START <= current_time <= self.MORNING_END:
            return True

        # Основная сессия
        if self.MAIN_START <= current_time <= self.MAIN_END:
            return True

        # Вечерняя сессия - ✅ проверяем через API, а не жёсткий список
        if self.EVENING_START <= current_time <= self.EVENING_END:
            # Если тикер указан, проверяем через API доступна ли торговля
            if ticker and self.bot:
                try:
                    figi = self.bot._get_figi_by_ticker(ticker)
                    if figi:
                        from trading_bot.api.tbank_client import tbank
                        status = tbank.get_trading_status(figi)
                        # Если API торговля доступна - можно торговать
                        if status.get('api_trade_available', False):
                            return True
                        # Если нет - проверяем, может быть это вечерняя сессия разрешена
                        return self._is_evening_trading_available(ticker)
                except Exception:
                    pass
            # Если тикер не указан или ошибка - разрешаем (бот сам проверит)
            return True

        return False

    def _is_evening_trading_available(self, ticker: str) -> bool:
        """
        Проверка, доступна ли вечерняя торговля для конкретного тикера
        ✅ НОВЫЙ МЕТОД: проверяет ликвидность и статус торгов
        """
        try:
            figi = self.bot._get_figi_by_ticker(ticker)
            if not figi:
                return False

            from trading_bot.api.tbank_client import tbank

            # Проверяем ликвидность в вечернюю сессию
            liquidity = tbank.check_liquidity(figi, required_volume=5000, min_depth=3)
            if liquidity.get('is_liquid', False):
                return True

            # Проверяем статус торгов
            status = tbank.get_trading_status(figi)
            if status.get('api_trade_available', False) and status.get('limit_order_available', False):
                return True

            return False
        except Exception:
            return True  # Если не удалось проверить - разрешаем

    def get_current_session(self) -> str:
        """Возвращает текущую сессию"""
        return self._current_session

    def get_minutes_to_end(self) -> Tuple[float, str]:
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