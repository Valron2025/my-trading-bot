"""Обработчик корректного завершения работы"""

from ..logger import info, warning


class ShutdownHandler:
    """Корректное завершение работы бота"""

    def __init__(self, bot):
        self.bot = bot

    def shutdown(self):
        """Корректное завершение"""
        info("🛑 Выполняется корректное завершение...")

        # Сохраняем состояние
        if hasattr(self.bot, 'db') and self.bot.db:
            self._save_state()

        # Закрываем позиции если нужно
        if self._should_close_positions():
            self._close_all_positions()

        warning("⏹️ Бот остановлен")

    def _save_state(self):
        """Сохранение состояния в БД"""
        try:
            from trading_bot.risk.position_manager import position_manager
            positions = position_manager.get_all_positions()
            position_data = {}
            for figi, pos in positions.items():
                position_data[figi] = {
                    'ticker': self.bot._get_ticker_by_figi(figi),
                    'quantity': pos.quantity,
                    'avg_price': pos.avg_price,
                    'side': pos.side.value if hasattr(pos.side, 'value') else str(pos.side),
                    'entry_time': pos.entry_time.isoformat()
                }
            self.bot.db.save_positions(position_data)
            self.bot.db.save_cycle_state(self.bot._cycle_count, self.bot._last_capital, 0)
            info("💾 Состояние бота сохранено в БД")
        except Exception as e:
            warning(f"Не удалось сохранить состояние: {e}")

    def _should_close_positions(self) -> bool:
        """Проверка, нужно ли закрывать позиции при остановке"""
        return False  # По умолчанию не закрываем

    def _close_all_positions(self):
        """
        ЗАКРЫТИЕ ВСЕХ ПОЗИЦИЙ ПРИ ОСТАНОВКЕ
        НО: НЕ ТРОГАЕМ ПРИБЫЛЬНЫЕ ПОЗИЦИИ!
        """
        try:
            # Используем правильный метод - закрываем только убыточные
            if hasattr(self.bot, '_emergency_close_profitable_only'):
                info("🛑 Остановка бота: закрываем только убыточные позиции")
                closed = self.bot._emergency_close_profitable_only()
                info(f"   ✅ Закрыто убыточных позиций: {closed}")
            elif hasattr(self.bot, 'emergency_close_all_positions'):
                warning("⚠️ Используется старый метод emergency_close_all_positions")
                warning("   Он закрывает ВСЕ позиции, включая прибыльные!")
                # Раскомментируйте следующую строку, если хотите использовать старый метод
                # self.bot.emergency_close_all_positions()
            else:
                # Fallback через position_manager
                from trading_bot.risk.position_manager import position_manager
                if hasattr(position_manager, 'emergency_close_worst_positions'):
                    position_manager.emergency_close_worst_positions(max_to_close=10)
                else:
                    warning("⚠️ Нет доступных методов для закрытия позиций")
        except Exception as e:
            warning(f"Ошибка при закрытии позиций: {e}")