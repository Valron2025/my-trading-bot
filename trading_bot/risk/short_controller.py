"""Управление SHORT позициями - автоматическое отключение при рисках"""

from ..config import config
from ..logger import warning, debug, error, info


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


def _get_telegram():
    from trading_bot.telegram.telegram_notifier import get_telegram_notifier
    return get_telegram_notifier()


class ShortController:
    """Контроллер SHORT позиций с автоматическим отключением"""

    def __init__(self, bot):
        self.bot = bot
        self._last_warning_time = 0
        self._last_status = None

    def auto_disable(self, total_capital: float) -> bool:
        """АВТОМАТИЧЕСКОЕ ОТКЛЮЧЕНИЕ SHORT"""
        short_disabled = False
        reasons = []

        # 1. СЛИШКОМ МАЛЕНЬКИЙ КАПИТАЛ
        if total_capital < 2000:
            short_disabled = True
            reasons.append(f"капитал {total_capital:.0f}₽ < 2000₽")

        # 2. Слишком высокая маржа
        try:
            margin_info = _get_tbank().get_margin_info()
            margin_rate = margin_info.get('margin_rate', 0) if margin_info else 0
            if margin_rate > 70:
                short_disabled = True
                reasons.append(f"маржа {margin_rate:.1f}% > 70%")
        except Exception as e:
            debug(f"Ошибка проверки маржи: {e}")

        # 3. Убыточные SHORT позиции
        try:
            positions = _get_tbank().get_positions()
            shorts = [p for p in positions if p['quantity'] < 0]
            if shorts:
                total_short_pnl = 0
                for pos in shorts:
                    current_price = _get_tbank().get_current_price(pos['figi'])
                    if current_price:
                        pnl = (pos['avg_price'] - current_price) * abs(pos['quantity'])
                        total_short_pnl += pnl
                if total_short_pnl < -200:
                    short_disabled = True
                    reasons.append(f"убыток по SHORT {total_short_pnl:.0f}₽")
        except:
            pass

        # Запоминаем текущий статус
        current_status = short_disabled

        # 4. Отключаем SHORT если нужно
        if short_disabled:
            config.use_short = False
            # ✅ ИСПРАВЛЕНО: НЕ МЕНЯЕМ ПОРОГ! Он уже рассчитан автоматически
            # Просто отключаем флаг use_short

            # Логируем ТОЛЬКО при изменении статуса
            if current_status != self._last_status:
                import time
                now = time.time()
                if now - self._last_warning_time > 60:
                    self._last_warning_time = now

                    info(f"\n{'=' * 50}")
                    info(f"🔻 SHORT АВТОМАТИЧЕСКИ ОТКЛЮЧЁН!")
                    for reason in reasons:
                        info(f"   Причина: {reason}")
                    info(f"{'=' * 50}\n")

                    # Отправляем уведомление в Telegram
                    telegram = _get_telegram()
                    if telegram:
                        telegram.send_warning(
                            f"🔻 SHORT АВТОМАТИЧЕСКИ ОТКЛЮЧЁН!\n"
                            f"Причины:\n" + "\n".join(f"• {r}" for r in reasons)
                        )
        else:
            # SHORT ВКЛЮЧЁН
            if current_status != self._last_status:
                config.use_short = True
                # ✅ ИСПРАВЛЕНО: НЕ ХАРДКОДИМ -2!
                # Порог уже рассчитан в config.get_adaptive_short_threshold()
                info(f"\n✅ SHORT АВТОМАТИЧЕСКИ ВКЛЮЧЁН (капитал {total_capital:.0f}₽)")
                info(f"   Порог SHORT: score ≤ {config.short_score_threshold}")

        self._last_status = current_status
        return short_disabled

    def can_open_short(self, total_capital: float) -> bool:
        """Проверка, можно ли открывать SHORT позицию"""
        if not config.use_short:
            return False

        if total_capital < 5000:
            return False

        try:
            margin_info = _get_tbank().get_margin_info()
            margin_rate = margin_info.get('margin_rate', 0)
            if margin_rate > 70:
                return False
        except Exception:
            pass

        return True