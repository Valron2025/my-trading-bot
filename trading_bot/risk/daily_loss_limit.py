"""Проверка дневного лимита убытка"""

from ..logger import error, debug
from ..utils.time_utils import get_moscow_time


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


def _get_telegram():
    from trading_bot.telegram.telegram_notifier import get_telegram_notifier
    return get_telegram_notifier()


class DailyLossLimitChecker:
    """Проверка и ограничение дневного убытка"""

    def __init__(self, bot):
        self.bot = bot
        self._last_limit_alert_date = None  # ✅ ДОБАВИТЬ

    def check(self) -> bool:
        """Проверка дневного лимита убытка"""
        try:
            today = get_moscow_time().date()

            # ✅ ДОБАВИТЬ ПРОВЕРКУ - не спамить уведомлениями
            if self._last_limit_alert_date == today:
                return False  # Уже уведомили сегодня

            if hasattr(self.bot, '_trades'):
                daily_trades = [t for t in self.bot._trades if t.get('date') == today]
            else:
                daily_trades = []

            if not daily_trades:
                return True

            daily_pnl = sum(t.get('pnl', 0) for t in daily_trades)

            _, total_capital, _ = _get_tbank().get_available_funds()

            daily_loss_limit = total_capital * 0.05

            if daily_pnl < -daily_loss_limit:
                self._last_limit_alert_date = today  # ✅ ЗАПОМНИТЬ ДАТУ

                error(f"\n{'=' * 60}")
                error(f"🚨 ДНЕВНОЙ ЛИМИТ УБЫТКА ДОСТИГНУТ!")
                error(f"   Убыток за день: {daily_pnl:.2f}₽")
                error(f"   Лимит: {daily_loss_limit:.2f}₽ (5% от капитала)")
                error(f"   Останавливаем торговлю до завтра")
                error(f"{'=' * 60}")

                telegram = _get_telegram()
                if telegram:
                    telegram.send_error(
                        f"🚨 ДНЕВНОЙ ЛИМИТ УБЫТКА ДОСТИГНУТ!\n"
                        f"Убыток: {daily_pnl:.2f}₽\n"
                        f"Лимит: {daily_loss_limit:.2f}₽\n"
                        f"Торговля остановлена до завтра"
                    )

                return False

            return True

        except Exception as e:
            debug(f"Ошибка в DailyLossLimitChecker: {e}")
            return True