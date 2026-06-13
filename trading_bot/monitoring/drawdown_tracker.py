"""Отслеживание просадок (drawdown)"""

import time
from typing import List

from ..logger import info, warning, debug


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


def _get_telegram():
    from trading_bot.telegram.telegram_notifier import get_telegram_notifier
    return get_telegram_notifier()


class DrawdownTracker:
    """Отслеживание просадок капитала"""

    def __init__(self, bot):
        self.bot = bot
        self._equity_history: List[float] = []
        self._max_drawdown_pct = 0
        self._last_drawdown_warning = 0
        self._max_points = 1000

    def add_point(self, value: float):
        """Добавление точки эквити"""
        self._equity_history.append(value)
        if len(self._equity_history) > self._max_points:
            self._equity_history = self._equity_history[-500:]

    def check(self):
        """Проверка текущей просадки"""
        try:
            if len(self._equity_history) < 10:
                return

            # Расчёт текущей просадки
            peak = max(self._equity_history)
            current = self._equity_history[-1] if self._equity_history else 0
            drawdown = (peak - current) if peak > 0 else 0
            drawdown_pct = (drawdown / peak * 100) if peak > 0 else 0

            # Максимальная просадка за всю историю
            max_drawdown, max_drawdown_pct = self._calculate_max_drawdown()

            # Получаем текущий капитал
            available, total_capital, _ = _get_tbank().get_available_funds()

            MAX_DRAWDOWN_PCT = 10.0

            info(f"📉 ПРОСАДКА: текущая {drawdown_pct:.1f}% ({drawdown:.0f}₽) | макс: {max_drawdown_pct:.1f}%")

            # Предупреждение при большой просадке
            if drawdown_pct > MAX_DRAWDOWN_PCT:
                warning(f"⚠️ ВНИМАНИЕ! ТЕКУЩАЯ ПРОСАДКА {drawdown_pct:.1f}% > {MAX_DRAWDOWN_PCT}%!")

                if time.time() - self._last_drawdown_warning > 3600:
                    telegram = _get_telegram()
                    if telegram:
                        telegram.send_warning(
                            f"⚠️ ВЫСОКАЯ ПРОСАДКА!\n"
                            f"Текущая: {drawdown_pct:.1f}% ({drawdown:.0f}₽)\n"
                            f"Максимальная: {max_drawdown_pct:.1f}%\n"
                            f"Капитал: {total_capital:.0f}₽"
                        )
                    self._last_drawdown_warning = time.time()

            self._max_drawdown_pct = max(self._max_drawdown_pct, max_drawdown_pct)

        except Exception as e:
            debug(f"Ошибка в DrawdownTracker: {e}")

    def _calculate_max_drawdown(self) -> tuple:
        """Расчёт максимальной просадки"""
        max_drawdown = 0
        max_drawdown_pct = 0
        running_peak = 0

        for value in self._equity_history:
            if value > running_peak:
                running_peak = value
            dd = running_peak - value
            dd_pct = (dd / running_peak * 100) if running_peak > 0 else 0
            if dd_pct > max_drawdown_pct:
                max_drawdown_pct = dd_pct
                max_drawdown = dd

        return max_drawdown, max_drawdown_pct