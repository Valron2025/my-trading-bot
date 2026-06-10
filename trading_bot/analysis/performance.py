"""Анализ эффективности торговли"""

from ..logger import info, warning, debug


def _get_telegram():
    from trading_bot.telegram.telegram_notifier import get_telegram_notifier
    return get_telegram_notifier()


class PerformanceAnalyzer:
    """Анализ эффективности торговли"""

    def __init__(self, bot):
        self.bot = bot

    def get_recent_trades(self, limit: int = 20):
        """✅ ДОБАВЛЕН ПУБЛИЧНЫЙ МЕТОД для получения сделок"""
        if hasattr(self.bot, 'get_trades_history'):
            return self.bot.get_trades_history()[-limit:]
        elif hasattr(self.bot, '_trades'):
            # Fallback для обратной совместимости
            return self.bot._trades[-limit:]
        return []

    def analyze(self):
        """Анализ эффективности за последние сделки"""
        try:
            # ✅ ИСПРАВЛЕНО: используем публичный метод вместо прямого доступа
            recent_trades = self.get_recent_trades(20)

            if not recent_trades:
                debug("📊 Анализ эффективности: нет сделок для анализа")
                return

            total_trades = len(recent_trades)
            winning_trades = [t for t in recent_trades if t.get('pnl', 0) > 0]
            losing_trades = [t for t in recent_trades if t.get('pnl', 0) < 0]

            win_rate = len(winning_trades) / total_trades * 100 if total_trades > 0 else 0

            avg_win = sum(t.get('pnl', 0) for t in winning_trades) / len(winning_trades) if winning_trades else 0
            avg_loss = abs(sum(t.get('pnl', 0) for t in losing_trades) / len(losing_trades)) if losing_trades else 0

            profit_factor = (sum(t.get('pnl', 0) for t in winning_trades) /
                             abs(sum(t.get('pnl', 0) for t in losing_trades))) if losing_trades else float('inf')

            total_pnl = sum(t.get('pnl', 0) for t in recent_trades)

            info(f"\n📊 АНАЛИЗ ЭФФЕКТИВНОСТИ (последние {total_trades} сделок):")
            info(f"   🎯 Win Rate: {win_rate:.1f}% ({len(winning_trades)}/{total_trades})")
            info(f"   💰 Общий P&L: {total_pnl:+.2f}₽")
            info(f"   📈 Средний выигрыш: {avg_win:+.2f}₽ | Средний убыток: {avg_loss:+.2f}₽")
            info(f"   ⚡ Profit Factor: {profit_factor:.2f}")

            if win_rate < 40 and total_trades >= 10:
                warning(f"   ⚠️ НИЗКИЙ WIN RATE ({win_rate:.1f}%)!")

            if profit_factor < 0.8 and total_trades >= 10:
                warning(f"   ⚠️ НИЗКИЙ PROFIT FACTOR ({profit_factor:.2f})!")

            if (win_rate < 35 or profit_factor < 0.7) and total_trades >= 15:
                telegram = _get_telegram()
                if telegram:
                    telegram.send_warning(
                        f"⚠️ НИЗКАЯ ЭФФЕКТИВНОСТЬ СТРАТЕГИИ!\n"
                        f"Win Rate: {win_rate:.1f}%\n"
                        f"Profit Factor: {profit_factor:.2f}\n"
                        f"P&L за {total_trades} сделок: {total_pnl:+.2f}₽"
                    )

        except Exception as e:
            debug(f"Ошибка в analyze_performance: {e}")