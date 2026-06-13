"""Защита от критической маржи"""

from typing import Tuple, Dict, Any

from ..logger import error, warning, debug


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


def _get_telegram():
    from trading_bot.telegram.telegram_notifier import get_telegram_notifier
    return get_telegram_notifier()


class MarginGuard:
    """Управление маржинальными рисками"""

    def __init__(self, bot):
        self.bot = bot

    def check_safety(self) -> Tuple[bool, float]:
        """Проверка безопасности маржи - автоматическая защита"""
        try:
            available, total, _ = _get_tbank().get_available_funds()
            margin_info = _get_tbank().get_margin_info()
            margin_rate = margin_info.get('margin_rate', 0)

            if margin_rate > 95:
                error(f"🚨 КРИТИЧЕСКАЯ МАРЖА: {margin_rate:.1f}%")
                if _get_telegram():
                    _get_telegram().send_error(f"🚨 КРИТИЧЕСКАЯ МАРЖА {margin_rate:.1f}%! Срочно пополните счёт!")
                return False, available

            if available < 100:
                warning(f"⚠️ КРИТИЧЕСКИ МАЛО СРЕДСТВ: {available:.0f}₽")
                if _get_telegram():
                    _get_telegram().send_warning(f"⚠️ Критически мало средств: {available:.0f}₽")
                return False, available

            # ✅ ИСПРАВЛЕНО: warning вместо error для малого капитала
            if margin_rate > 80:
                if total < 5000:
                    warning(f"⚠️ ВЫСОКАЯ МАРЖА: {margin_rate:.1f}% (малый капитал)")
                else:
                    warning(f"⚠️ ВЫСОКАЯ МАРЖА: {margin_rate:.1f}%, доступно {available:.0f}₽")

                if margin_rate > 85 and _get_telegram():
                    _get_telegram().send_warning(f"⚠️ ВЫСОКАЯ МАРЖА {margin_rate:.1f}%! Рекомендуется пополнить счёт")

            return True, available

        except Exception as e:
            debug(f"Ошибка проверки маржи: {e}")
            return True, 0

    def get_margin_status(self) -> Dict[str, Any]:
        """Получение статуса маржинальной торговли"""
        try:
            margin_allowed, margin_reason = _get_tbank().check_margin_trading_allowed()

            if not margin_allowed:
                return {
                    'status': 'disabled',
                    'warning': margin_reason,
                    'critical': False,
                    'margin_rate': 0,
                    'margin_trading_enabled': False
                }

            margin_info = _get_tbank().get_margin_info()
            if not margin_info:
                return {'status': 'unknown', 'critical': False, 'margin_rate': 0}

            margin_rate = margin_info.get('margin_rate', 0)

            if margin_rate >= 85:
                status, critical = 'critical', True
            elif margin_rate >= 70:
                status, critical = 'warning', False
            else:
                status, critical = 'ok', False

            return {
                'status': status,
                'critical': critical,
                'margin_rate': margin_rate,
                'available_margin': margin_info.get('available_margin', 0),
                'used_margin': margin_info.get('used_margin', 0),
                'margin_trading_enabled': margin_allowed
            }

        except Exception as e:
            error(f"Ошибка проверки маржи: {e}")
            return {'status': 'error', 'critical': False, 'margin_rate': 0}