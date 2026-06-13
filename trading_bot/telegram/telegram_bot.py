# telegram_bot.py - ИСПРАВЛЕННАЯ ВЕРСИЯ

import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from trading_bot.logger import success, error, debug


def _get_telegram():
    from .telegram_notifier import get_telegram_notifier
    return get_telegram_notifier()


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


MOSCOW_TZ = timezone(timedelta(hours=3))


def now_msk() -> datetime:
    return datetime.now(MOSCOW_TZ)


class OrderState:
    IDLE = "idle"
    WAITING_TICKER = "waiting_ticker"
    WAITING_QUANTITY = "waiting_quantity"
    WAITING_PRICE = "waiting_price"
    WAITING_CONFIRMATION = "waiting_confirmation"


class TelegramBot:
    def __init__(self, trading_bot=None):
        self.trading_bot = trading_bot
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._start_time = time.time()
        self.user_states: Dict[int, Dict[str, Any]] = {}
        self._cached_status = None
        self._cached_status_time = 0
        self._cache_ttl = 15
        self._waiting_for_value = None
        self._last_response_time: Dict[int, float] = {}  # Для rate limiting

    def _send_message(self, message: str, parse_mode: str = "HTML"):
        telegram = _get_telegram()
        if telegram and telegram.enabled:
            telegram.send_async(message, parse_mode)
        else:
            print(f"⚠️ Telegram не доступен: {message[:50]}...")

    def _get_user_state(self, chat_id: int) -> Dict[str, Any]:
        if chat_id not in self.user_states:
            self.user_states[chat_id] = {'state': OrderState.IDLE, 'data': {}}
        return self.user_states[chat_id]

    def _clear_user_state(self, chat_id: int):
        if chat_id in self.user_states:
            self.user_states[chat_id] = {'state': OrderState.IDLE, 'data': {}}

    # ========== REPLY KEYBOARD ==========

    def get_reply_menu_buttons(self) -> List[str]:
        return [
            "📊 Статус", "💰 Баланс", "📈 Позиции", "💼 Портфель",
            "📉 P&L", "📊 Маржа", "🟢 Купить", "🔴 Продать",
            "🔒 Закрыть всё", "⚙️ Настройки", "🛠️ Сервис", "❓ Помощь"
        ]

    def send_reply_menu(self, chat_id: int):
        telegram = _get_telegram()
        if not telegram or not telegram.enabled:
            return False

        buttons = self.get_reply_menu_buttons()

        try:
            return telegram.send_reply_keyboard(chat_id, "📱 <b>ГЛАВНОЕ МЕНЮ</b>\n\nВыберите действие:", buttons)
        except Exception as e:
            debug(f"Ошибка отправки меню: {e}")
            return False

    # ========== НАСТРОЙКИ ==========

    def _show_settings_menu(self, chat_id: int):
        try:
            from trading_bot.core.settings_manager import settings_manager
            if settings_manager is None:
                self._send_message("⚠️ Меню настроек временно недоступно")
                self._send_message(self.get_config_text())
                return

            text = settings_manager.get_settings_text()
            self._send_message(text)

            buttons = [
                "1️⃣ Тейк-профит", "2️⃣ Стоп-лосс", "3️⃣ Трейлинг",
                "4️⃣ Макс.позиций", "5️⃣ Мин.сделка", "6️⃣ Размер",
                "7️⃣ Score", "8️⃣ Таймаут", "9️⃣ Цикл", "🔟 SHORT",
                "📊 Сброс", "🔄 АВТО", "🔙 Назад"
            ]

            telegram = _get_telegram()
            if telegram and telegram.enabled:
                telegram.send_reply_keyboard(chat_id, "📱 Выберите параметр для изменения:", buttons)

        except ImportError:
            self._send_message("⚠️ Модуль настроек не найден")
            self._send_message(self.get_config_text())
        except Exception as e:
            self._send_message(f"❌ Ошибка: {e}")
            self._send_message(self.get_config_text())

    def process_settings_input(self, chat_id: int, text: str) -> bool:
        from trading_bot.core.settings_manager import settings_manager

        if self._waiting_for_value and self._waiting_for_value.get('chat_id') == chat_id:
            return self._process_setting_value(chat_id, text)

        # Обработка кнопок настроек
        if text == "1️⃣ Тейк-профит":
            self._waiting_for_value = {'param': 'tp', 'chat_id': chat_id}
            self._send_message("📝 Введите новый Тейк-профит (например: 1.0):\n" +
                               f"📊 Текущий: +{settings_manager.get('take_profit_pct'):.1f}%")
            return True
        elif text == "2️⃣ Стоп-лосс":
            self._waiting_for_value = {'param': 'sl', 'chat_id': chat_id}
            self._send_message("📝 Введите новый Стоп-лосс (например: 0.5):\n" +
                               f"📊 Текущий: -{settings_manager.get('stop_loss_pct'):.1f}%")
            return True
        elif text == "3️⃣ Трейлинг":
            self._waiting_for_value = {'param': 'trail', 'chat_id': chat_id}
            self._send_message("📝 Введите новый Трейлинг-стоп (например: 0.3):\n" +
                               f"📊 Текущий: {settings_manager.get('trailing_stop_pct'):.2f}%")
            return True
        elif text == "4️⃣ Макс.позиций":
            self._waiting_for_value = {'param': 'max_pos', 'chat_id': chat_id}
            self._send_message("📝 Введите макс. количество позиций (1-5):\n" +
                               f"📊 Текущее: {settings_manager.get('max_positions')}")
            return True
        elif text == "5️⃣ Мин.сделка":
            self._waiting_for_value = {'param': 'min_amount', 'chat_id': chat_id}
            self._send_message("📝 Введите мин. сумму сделки (50-1000₽):\n" +
                               f"📊 Текущая: {settings_manager.get('min_trade_amount')}₽")
            return True
        elif text == "6️⃣ Размер":
            self._waiting_for_value = {'param': 'pos_size', 'chat_id': chat_id}
            self._send_message("📝 Введите размер позиции (5-30%):\n" +
                               f"📊 Текущий: {settings_manager.get('position_size_pct')}%")
            return True
        elif text == "7️⃣ Score":
            self._waiting_for_value = {'param': 'score', 'chat_id': chat_id}
            self._send_message("📝 Введите порог Score (0-5):\n" +
                               f"📊 Текущий: ≥{settings_manager.get('score_threshold_long')}")
            return True
        elif text == "8️⃣ Таймаут":
            self._waiting_for_value = {'param': 'timeout', 'chat_id': chat_id}
            self._send_message("📝 Введите таймаут (5-60 мин):\n" +
                               f"📊 Текущий: {settings_manager.get('timeout_minutes')} мин")
            return True
        elif text == "9️⃣ Цикл":
            self._waiting_for_value = {'param': 'cycle', 'chat_id': chat_id}
            self._send_message("📝 Введите интервал цикла (5-60 сек):\n" +
                               f"📊 Текущий: {settings_manager.get('cycle_seconds')} сек")
            return True
        elif text == "🔟 SHORT":
            current = settings_manager.get('short_enabled')
            new_value = not current
            settings_manager.set('short_enabled', new_value)
            self._send_message(f"🔻 SHORT торговля {'✅ ВКЛЮЧЕНА' if new_value else '❌ ОТКЛЮЧЕНА'}")
            self._show_settings_menu(chat_id)
            return True
        elif text == "📊 Сброс":
            for key, value in settings_manager.DEFAULT_SETTINGS.items():
                settings_manager.set(key, value)
            self._send_message("✅ Настройки сброшены до значений по умолчанию")
            self._show_settings_menu(chat_id)
            return True
        elif text == "🔄 АВТО":
            self._send_message("🔄 Восстанавливаю автоматические настройки...")
            if self.trading_bot:
                try:
                    tbank = _get_tbank()
                    _, total, _ = tbank.get_available_funds()

                    from trading_bot.config import config
                    if hasattr(self.trading_bot, 'trading_loop'):
                        self.trading_bot.trading_loop._adaptive_configuration(total)

                        settings_manager.set('take_profit_pct', config.take_profit_pct)
                        settings_manager.set('stop_loss_pct', config.stop_loss_pct)
                        settings_manager.set('max_positions', config.max_positions)
                        settings_manager.set('position_size_pct', config.adaptive_position_size_pct * 100)
                        settings_manager.set('score_threshold_long', config.long_score_threshold)
                        settings_manager.set('short_enabled', config.use_short)

                        self._send_message(
                            f"🔄 <b>АВТОМАТИЧЕСКИЕ НАСТРОЙКИ ВОССТАНОВЛЕНЫ</b>\n\n"
                            f"📊 <b>НОВЫЕ ПАРАМЕТРЫ:</b>\n"
                            f"🎯 Тейк-профит: <b>+{config.take_profit_pct:.1f}%</b>\n"
                            f"🛑 Стоп-лосс: <b>-{config.stop_loss_pct:.1f}%</b>\n"
                            f"📈 Макс. позиций: <b>{config.max_positions}</b>\n"
                            f"📊 Размер позиции: <b>{config.adaptive_position_size_pct * 100:.0f}%</b>\n"
                            f"🎫 Score порог LONG: <b>≥ {config.long_score_threshold}</b>\n"
                            f"🔻 SHORT: <b>{'✅ Вкл' if config.use_short else '❌ Выкл'}</b>\n\n"
                            f"💡 Настройки адаптированы под капитал <b>{total:.0f}₽</b>"
                        )
                    else:
                        self._send_message("❌ Метод адаптивной настройки не найден")
                except Exception as e:
                    self._send_message(f"❌ Ошибка: {e}")
            else:
                self._send_message("❌ Торговый бот не доступен")

            self._show_settings_menu(chat_id)
            return True
        elif text == "🔙 Назад":
            self.send_reply_menu(chat_id)
            return True
        return False

    def _process_setting_value(self, chat_id: int, text: str) -> bool:
        from trading_bot.core.settings_manager import settings_manager
        waiting = self._waiting_for_value
        if not waiting or waiting.get('chat_id') != chat_id:
            return False

        try:
            value = float(text.replace(',', '.'))
            param = waiting['param']

            if param == 'tp':
                if 0.3 <= value <= 3.0:
                    settings_manager.set('take_profit_pct', value)
                    self._send_message(f"✅ Тейк-профит изменён на +{value:.1f}%")
            elif param == 'sl':
                if 0.2 <= value <= 2.0:
                    settings_manager.set('stop_loss_pct', value)
                    self._send_message(f"✅ Стоп-лосс изменён на -{value:.1f}%")
            elif param == 'trail':
                if 0.1 <= value <= 1.0:
                    settings_manager.set('trailing_stop_pct', value)
                    self._send_message(f"✅ Трейлинг-стоп изменён на {value:.2f}%")
            elif param == 'max_pos':
                value = int(value)
                if 1 <= value <= 5:
                    settings_manager.set('max_positions', value)
                    self._send_message(f"✅ Макс. позиций изменено на {value}")
            elif param == 'min_amount':
                value = int(value)
                if 50 <= value <= 1000:
                    settings_manager.set('min_trade_amount', value)
                    self._send_message(f"✅ Мин. сумма изменена на {value}₽")
            elif param == 'pos_size':
                if 5 <= value <= 30:
                    settings_manager.set('position_size_pct', value)
                    self._send_message(f"✅ Размер позиции изменён на {value:.0f}%")
            elif param == 'score':
                value = int(value)
                if 0 <= value <= 5:
                    settings_manager.set('score_threshold_long', value)
                    settings_manager.set('score_threshold_short', -value)
                    self._send_message(f"✅ Score порог изменён на ≥{value} (LONG) и ≤{-value} (SHORT)")
            elif param == 'timeout':
                value = int(value)
                if 5 <= value <= 60:
                    settings_manager.set('timeout_minutes', value)
                    self._send_message(f"✅ Таймаут изменён на {value} мин")
            elif param == 'cycle':
                value = int(value)
                if 5 <= value <= 60:
                    settings_manager.set('cycle_seconds', value)
                    self._send_message(f"✅ Цикл изменён на {value} сек")

            self._waiting_for_value = None
        except ValueError:
            self._send_message("❌ Ошибка: введите число")

        self._show_settings_menu(chat_id)
        return True

    # ========== ИНФОРМАЦИОННЫЕ МЕТОДЫ ==========

    def get_status_text(self, chat_id: int = None) -> str:
        if not self.trading_bot:
            return "❌ Торговый бот не инициализирован"
        try:
            from trading_bot.config import config
            tbank = _get_tbank()
            available, total, _ = tbank.get_available_funds()

            total_pnl = 0
            total_pnl_pct = 0
            positions_count = 0

            if hasattr(self.trading_bot, 'get_detailed_pnl'):
                pnl_data = self.trading_bot.get_detailed_pnl()
                if pnl_data:
                    total_pnl = pnl_data.get('total_pnl', 0)
                    total_pnl_pct = pnl_data.get('total_pnl_pct', 0)
                    positions = pnl_data.get('positions')
                    if positions:
                        positions_count = len(positions)

            return (
                f"📊 <b>СТАТУС БОТА</b>\n\n"
                f"💰 Капитал: <b>{total:.2f}</b> ₽\n"
                f"💵 Свободно: <b>{available:.2f}</b> ₽\n"
                f"📈 Позиций: <b>{positions_count}</b>\n"
                f"📊 P&L общий: <b>{total_pnl:+.2f}</b> ₽ (<b>{total_pnl_pct:+.2f}%</b>)\n"
                f"🎯 Режим: <b>{'Микро' if total < 5000 else 'Стандартный'}</b>\n"
                f"⚙️ Тейк: +{config.take_profit_pct:.1f}% | Стоп: -{config.stop_loss_pct:.1f}%\n"
                f"⏱ {now_msk().strftime('%H:%M:%S')} МСК"
            )
        except Exception as e:
            error(f"Ошибка в get_status_text: {e}")
            return f"❌ Ошибка: {str(e)[:50]}"

    def get_balance_text(self) -> str:
        if not self.trading_bot:
            return "❌ Торговый бот не инициализирован"
        try:
            tbank = _get_tbank()
            available, total, _ = tbank.get_available_funds()
            return (
                f"💰 <b>БАЛАНС</b>\n\n"
                f"💵 Свободно: <b>{available:.2f}</b> ₽\n"
                f"💎 Капитал: <b>{total:.2f}</b> ₽\n"
                f"⏱ {now_msk().strftime('%H:%M:%S')} МСК"
            )
        except Exception as e:
            return f"❌ Ошибка: {str(e)[:50]}"

    def get_positions_text(self) -> str:
        if not self.trading_bot:
            return "❌ Торговый бот не инициализирован"
        try:
            if hasattr(self.trading_bot, 'get_detailed_pnl'):
                pnl_data = self.trading_bot.get_detailed_pnl()
            else:
                pnl_data = {}
            if not pnl_data or not pnl_data.get('positions'):
                return "📭 <b>Нет открытых позиций</b>"
            message = "📈 <b>ОТКРЫТЫЕ ПОЗИЦИИ</b>\n\n"
            for i, pos in enumerate(pnl_data['positions'][:10], 1):
                side = pos['side']
                side_emoji = "🔴" if side == "SHORT" else "🟢"
                profit_icon = "🟢" if pos['net_pnl'] > 0 else "🔴" if pos['net_pnl'] < 0 else "⚪"
                ticker = pos.get('ticker', pos['figi'][:12])
                message += (
                    f"{i}. {side_emoji} <b>{side}</b> {ticker}\n"
                    f"   📦 {pos['quantity']} шт @ {pos['avg_price']:.2f}₽\n"
                    f"   💰 Текущая: {pos['current_price']:.2f}₽\n"
                    f"   {profit_icon} P&L: <b>{pos['net_pnl']:+.2f}</b>₽ (<b>{pos['pnl_pct']:+.2f}%</b>)\n"
                )
            message += f"\n📊 Всего: <b>{len(pnl_data['positions'])}</b> позиций"
            return message
        except Exception as e:
            return f"❌ Ошибка: {str(e)[:50]}"

    def get_portfolio_text(self) -> str:
        return self.get_positions_text()

    def get_pnl_text(self) -> str:
        if not self.trading_bot:
            return "❌ Торговый бот не инициализирован"
        try:
            if hasattr(self.trading_bot, 'get_detailed_pnl'):
                pnl_data = self.trading_bot.get_detailed_pnl()
            else:
                pnl_data = {}
            if not pnl_data:
                return "📊 <b>ОТЧЁТ P&L</b>\n\nНет данных"

            positions = pnl_data.get('positions', [])
            if not positions:
                return "📊 <b>ОТЧЁТ P&L</b>\n\nНет открытых позиций"

            total_pnl = pnl_data.get('total_pnl', 0)
            total_pnl_pct = pnl_data.get('total_pnl_pct', 0)
            winning = sum(1 for p in positions if p.get('net_pnl', 0) > 0)
            losing = sum(1 for p in positions if p.get('net_pnl', 0) < 0)
            win_rate = (winning / (winning + losing) * 100) if (winning + losing) > 0 else 0

            message = "📊 <b>ОТЧЁТ P&L</b>\n\n"
            for pos in positions[:5]:
                ticker = pos.get('ticker', pos['figi'][:12])
                profit_icon = "🟢" if pos['net_pnl'] > 0 else "🔴" if pos['net_pnl'] < 0 else "⚪"
                message += f"{profit_icon} <b>{ticker}</b>: {pos['pnl_pct']:+.1f}% ({pos['net_pnl']:+.2f}₽)\n"

            message += f"\n━━━━━━━━━━━━━━━━━━━━━\n"
            message += f"📊 Всего позиций: <b>{len(positions)}</b>\n"
            message += f"✅ Прибыльных: <b>{winning}</b> | ❌ Убыточных: <b>{losing}</b>\n"
            message += f"📈 Win Rate: <b>{win_rate:.1f}%</b>\n"
            message += f"{'🟢' if total_pnl > 0 else '🔴'} Общий P&L: <b>{total_pnl:+.2f}</b> ₽ (<b>{total_pnl_pct:+.2f}%</b>)\n"
            return message
        except Exception as e:
            return f"❌ Ошибка: {str(e)[:50]}"

    def get_margin_text(self) -> str:
        if not self.trading_bot:
            return "❌ Торговый бот не инициализирован"
        try:
            margin_status = self.trading_bot.get_margin_status()
            status_emoji = "🟢✅" if margin_status.get('status') == 'ok' else "🟡⚠️" if margin_status.get(
                'status') == 'warning' else "🔴🔥"
            return (
                f"📊 <b>МАРЖИНАЛЬНЫЙ СТАТУС</b> {status_emoji}\n\n"
                f"💰 Ликвидный портфель: <b>{margin_status.get('liquid_portfolio', 0):.2f}</b> ₽\n"
                f"🔒 Использовано маржи: <b>{margin_status.get('used_margin', 0):.2f}</b> ₽\n"
                f"✅ Доступно маржи: <b>{margin_status.get('available_margin', 0):.2f}</b> ₽\n"
                f"📊 Процент использования: <b>{margin_status.get('margin_rate', 0):.1f}%</b>"
            )
        except Exception as e:
            return f"❌ Ошибка: {str(e)[:50]}"

    def get_config_text(self) -> str:
        try:
            from trading_bot.config import config
            return (
                f"⚙️ <b>ТЕКУЩИЕ НАСТРОЙКИ</b>\n\n"
                f"🎯 Тейк-профит: <b>+{config.take_profit_pct:.1f}%</b>\n"
                f"🛑 Стоп-лосс: <b>-{config.stop_loss_pct:.1f}%</b>\n"
                f"🔻 Трейлинг-стоп: <b>{config.trailing_stop_pct:.2f}%</b>\n"
                f"⏰ Таймаут: <b>{config.adaptive_timeout_minutes} мин</b>\n"
                f"📊 Макс. позиций: <b>{config.max_positions}</b>\n"
                f"💰 Мин. сделка: <b>{config.min_trade_amount}₽</b>\n"
                f"📈 Размер позиции: <b>{config.adaptive_position_size_pct * 100:.0f}%</b>\n"
                f"🎫 Score порог: <b>{config.long_score_threshold}</b>\n"
                f"🔄 Цикл: <b>{config.adaptive_cycle_seconds} сек</b>"
            )
        except Exception as e:
            return f"❌ Ошибка: {str(e)[:50]}"

    def get_help_text(self) -> str:
        return (
            "🤖 <b>ТОРГОВЫЙ БОТ - ПОМОЩЬ</b>\n\n"
            "📱 <b>ИСПОЛЬЗУЙТЕ МЕНЮ ВНИЗУ ЭКРАНА:</b>\n"
            "   Просто нажмите на нужную кнопку\n\n"
            "📊 <b>ДОСТУПНЫЕ ДЕЙСТВИЯ:</b>\n"
            "   • Статус - состояние бота\n"
            "   • Баланс - средства на счёте\n"
            "   • Позиции - открытые позиции\n"
            "   • Портфель - состав портфеля\n"
            "   • P&L - прибыль/убыток\n"
            "   • Маржа - маржинальный статус\n"
            "   • Купить - ручная покупка\n"
            "   • Продать - ручная продажа\n"
            "   • Закрыть всё - закрыть все позиции\n"
            "   • Настройки - параметры бота\n"
            "   • Сервис - сервисные функции"
        )

    # ========== МЕТОДЫ ТОРГОВЛИ ==========

    def start_buy_flow(self, chat_id: int):
        self._clear_user_state(chat_id)
        state = self._get_user_state(chat_id)
        state['state'] = OrderState.WAITING_TICKER
        state['data'] = {'direction': 'BUY'}
        self._send_message(
            "🟢 <b>ПОКУПКА АКТИВА</b>\n\n"
            "Введите тикер акции (например, SBER, GAZP, LKOH):\n\n"
            "❌ Для отмены введите /cancel"
        )

    def start_sell_flow(self, chat_id: int):
        self._clear_user_state(chat_id)
        state = self._get_user_state(chat_id)
        state['state'] = OrderState.WAITING_TICKER
        state['data'] = {'direction': 'SELL'}
        self._send_message(
            "🔴 <b>ПРОДАЖА АКТИВА</b>\n\n"
            "Введите тикер акции (например, SBER, GAZP, LKOH):\n\n"
            "❌ Для отмены введите /cancel"
        )

    def process_ticker_input(self, chat_id: int, ticker: str):
        state = self._get_user_state(chat_id)
        if not self.trading_bot:
            self._send_message("❌ Торговый бот не доступен")
            self._clear_user_state(chat_id)
            return

        ticker = ticker.upper().strip()
        try:
            tbank = _get_tbank()
            all_shares = tbank.get_all_shares()
            figi = None
            stock_name = None
            lot = None
            for stock in all_shares:
                if stock.get('ticker') == ticker and stock.get('currency') == 'rub':
                    figi = stock['figi']
                    stock_name = stock['name']
                    lot = stock['lot']
                    break

            if not figi:
                self._send_message(f"❌ Тикер {ticker} не найден. Попробуйте другой.")
                return

            current_price = tbank.get_current_price(figi)
            if not current_price:
                self._send_message(f"❌ Не удалось получить цену для {ticker}")
                return

            state['data']['ticker'] = ticker
            state['data']['figi'] = figi
            state['data']['stock_name'] = stock_name
            state['data']['lot'] = lot
            state['data']['current_price'] = current_price
            state['state'] = OrderState.WAITING_QUANTITY

            self._send_message(
                f"📊 <b>ИНФОРМАЦИЯ О {ticker}</b>\n\n"
                f"📝 Название: {stock_name[:40]}\n"
                f"💰 Текущая цена: <b>{current_price:.2f}</b> ₽\n"
                f"📦 Лот: {lot} шт\n\n"
                f"Введите количество (кратно {lot}):"
            )
        except Exception as e:
            self._send_message(f"❌ Ошибка: {str(e)[:100]}")
            self._clear_user_state(chat_id)

    def process_quantity_input(self, chat_id: int, quantity_str: str):
        state = self._get_user_state(chat_id)
        try:
            quantity = int(quantity_str)
            lot = state['data'].get('lot', 1)
            current_price = state['data']['current_price']

            if quantity <= 0:
                raise ValueError
            if quantity % lot != 0:
                self._send_message(f"❌ Количество должно быть кратно {lot} (лот).")
                return

            direction = state['data']['direction']
            total_cost = quantity * current_price

            tbank = _get_tbank()
            available, total, _ = tbank.get_available_funds()

            if direction == 'BUY' and total_cost > available * 0.95:
                self._send_message(
                    f"❌ Недостаточно средств!\n\n"
                    f"💰 Требуется: <b>{total_cost:.2f}</b> ₽\n"
                    f"💵 Доступно: <b>{available:.2f}</b> ₽\n"
                    f"Уменьшите количество"
                )
                return

            state['data']['quantity'] = quantity
            state['data']['total_cost'] = total_cost
            state['state'] = OrderState.WAITING_PRICE

            self._send_message(
                f"📝 Количество: <b>{quantity}</b> шт\n"
                f"💰 Сумма: <b>{total_cost:.2f}</b> ₽\n\n"
                f"Введите цену (или 0 для рыночной):\n"
                f"💡 Текущая цена: {current_price:.2f}₽"
            )
        except ValueError:
            self._send_message("❌ Некорректное количество. Введите целое положительное число:")

    def process_price_input(self, chat_id: int, price_str: str):
        state = self._get_user_state(chat_id)
        try:
            if price_str == "0" or price_str.lower() == "рыночная":
                price = 0
                price_text = "Рыночная"
            else:
                price = float(price_str.replace(',', '.'))
                if price <= 0:
                    raise ValueError
                price_text = f"{price:.2f} ₽"

            state['data']['price'] = price
            state['state'] = OrderState.WAITING_CONFIRMATION

            direction = state['data']['direction']
            ticker = state['data']['ticker']
            quantity = state['data']['quantity']
            current_price = state['data']['current_price']
            action_text = "ПОКУПКА" if direction == 'BUY' else "ПРОДАЖА"
            action_emoji = "🟢" if direction == 'BUY' else "🔴"
            final_price = price if price > 0 else current_price
            final_total = quantity * final_price

            message = (
                f"{action_emoji} <b>ПОДТВЕРЖДЕНИЕ {action_text}</b>\n\n"
                f"📊 Тикер: <b>{ticker}</b>\n"
                f"🔢 Количество: <b>{quantity}</b> шт\n"
                f"💰 Цена: <b>{price_text}</b>\n"
                f"💵 Ориентир: <b>{final_total:.2f}</b> ₽\n\n"
                f"✅ Подтвердите операцию командой <b>/confirm</b>\n"
                f"❌ Или отмените командой <b>/cancel</b>"
            )
            self._send_message(message)
        except ValueError:
            self._send_message("❌ Некорректная цена. Введите число больше 0 или 0 для рыночной:")

    def execute_order(self, chat_id: int):
        state = self._get_user_state(chat_id)
        if state['state'] != OrderState.WAITING_CONFIRMATION:
            self._send_message("❌ Нет активной заявки для подтверждения")
            return

        direction = state['data']['direction']
        ticker = state['data']['ticker']
        figi = state['data']['figi']
        quantity = state['data']['quantity']
        price = state['data'].get('price', 0)

        self._send_message(f"⏳ Исполнение заявки на {direction} {ticker}...")

        try:
            tbank = _get_tbank()
            if direction == 'BUY':
                if price > 0:
                    success_flag = tbank.place_pending_order(figi, quantity, "BUY", price)
                else:
                    success_flag = tbank.buy(figi, quantity)
            else:
                if price > 0:
                    success_flag = tbank.place_pending_order(figi, quantity, "SELL", price)
                else:
                    success_flag = tbank.sell(figi, quantity)

            if success_flag:
                self._send_message(
                    f"✅ <b>ЗАЯВКА ИСПОЛНЕНА</b>\n\n"
                    f"📊 {direction} {quantity} {ticker}\n"
                    f"{'💰 Лимит: ' + str(price) + '₽' if price > 0 else '🏷 Рыночная'}\n"
                    f"💵 Сумма: {quantity * (price if price > 0 else state['data']['current_price']):.2f}₽"
                )
            else:
                self._send_message(f"❌ Не удалось исполнить заявку. Проверьте баланс.")
        except Exception as e:
            self._send_message(f"❌ Ошибка: {str(e)[:100]}")

        self._clear_user_state(chat_id)

    def cancel_order(self, chat_id: int):
        self._clear_user_state(chat_id)
        self._send_message("❌ Операция отменена")

    def close_all_positions(self, chat_id: int):
        if not self.trading_bot:
            self._send_message("❌ Торговый бот не доступен")
            return

        try:
            tbank = _get_tbank()
            positions = self.trading_bot._get_positions() if hasattr(self.trading_bot, '_get_positions') else []
            if not positions:
                self._send_message("📭 Нет открытых позиций для закрытия")
                return

            self._send_message(f"⏳ Закрытие {len(positions)} позиций...")
            closed = 0
            for pos in positions:
                figi = pos['figi']
                quantity = abs(pos['quantity'])
                if pos['quantity'] < 0:
                    if tbank.buy(figi, quantity):
                        closed += 1
                else:
                    if tbank.sell(figi, quantity):
                        closed += 1

            self._send_message(f"✅ Закрыто позиций: <b>{closed}</b> из {len(positions)}")
        except Exception as e:
            self._send_message(f"❌ Ошибка: {str(e)[:100]}")

    def clear_validation_cache(self):
        if self.trading_bot:
            self.trading_bot.clear_validation_cache()
            self._send_message("✅ Кеш валидации очищен")

    # ========== ОБРАБОТКА КОМАНД ==========

    def process_command(self, command: str, chat_id: int = None) -> bool:
        if not command or not chat_id:
            return False

        # Rate limiting (не чаще 1 сообщения в секунду)
        now = time.time()
        if chat_id in self._last_response_time:
            if now - self._last_response_time[chat_id] < 1:
                return True
        self._last_response_time[chat_id] = now

        # Обработка ввода настроек
        if self._waiting_for_value and self._waiting_for_value.get('chat_id') == chat_id:
            return self._process_setting_value(chat_id, command)

        cmd = command.lower().strip()

        # Обработка кнопок меню
        if command == "📊 Статус" or cmd == "/status":
            self._send_message(self.get_status_text(chat_id))
            return True
        elif command == "💰 Баланс" or cmd == "/balance":
            self._send_message(self.get_balance_text())
            return True
        elif command == "📈 Позиции" or cmd == "/positions":
            self._send_message(self.get_positions_text())
            return True
        elif command == "💼 Портфель" or cmd == "/portfolio":
            self._send_message(self.get_portfolio_text())
            return True
        elif command == "📉 P&L" or cmd == "/pnl":
            self._send_message(self.get_pnl_text())
            return True
        elif command == "📊 Маржа" or cmd == "/margin":
            self._send_message(self.get_margin_text())
            return True
        elif command == "🟢 Купить" or cmd == "/buy":
            self.start_buy_flow(chat_id)
            return True
        elif command == "🔴 Продать" or cmd == "/sell":
            self.start_sell_flow(chat_id)
            return True
        elif command == "🔒 Закрыть всё" or cmd == "/close_all":
            self.close_all_positions(chat_id)
            return True
        elif command == "⚙️ Настройки" or cmd == "/settings":
            self._show_settings_menu(chat_id)
            return True
        elif command == "🛠️ Сервис" or cmd == "/service":
            self._send_message(
                "📊 <b>СЕРВИСНЫЕ ФУНКЦИИ</b>\n\n"
                "/clear_cache - Очистить кеш валидации\n"
                "/health - Проверка здоровья\n"
                "/test - Тест подключения"
            )
            return True
        elif command == "❓ Помощь" or cmd == "/help":
            self._send_message(self.get_help_text())
            return True
        elif cmd == "/start" or cmd == "/menu":
            self.send_reply_menu(chat_id)
            return True
        elif cmd == "/clear_cache":
            self.clear_validation_cache()
            return True
        elif cmd == "/health":
            self._send_message("❤️ <b>HEALTH CHECK</b>\n\n✅ Бот работает")
            return True
        elif cmd == "/test":
            start = time.time()
            try:
                tbank = _get_tbank()
                available, total, _ = tbank.get_available_funds()
                api_time = (time.time() - start) * 1000
                self._send_message(
                    f"✅ <b>ТЕСТ ПОДКЛЮЧЕНИЯ</b>\n\n"
                    f"✅ API Т-Банк: <b>{api_time:.0f}ms</b>\n"
                    f"💰 Баланс: {total:.2f}₽\n"
                    f"✅ Telegram: <b>OK</b>"
                )
            except Exception as e:
                self._send_message(f"❌ Ошибка теста: {str(e)[:50]}")
            return True

        # Обработка шагов создания ордера
        state = self._get_user_state(chat_id)

        if state['state'] == OrderState.WAITING_TICKER:
            if command.lower() == "/cancel":
                self.cancel_order(chat_id)
                return True
            self.process_ticker_input(chat_id, command)
            return True
        elif state['state'] == OrderState.WAITING_QUANTITY:
            if command.lower() == "/cancel":
                self.cancel_order(chat_id)
                return True
            self.process_quantity_input(chat_id, command)
            return True
        elif state['state'] == OrderState.WAITING_PRICE:
            if command.lower() == "/cancel":
                self.cancel_order(chat_id)
                return True
            self.process_price_input(chat_id, command)
            return True
        elif state['state'] == OrderState.WAITING_CONFIRMATION:
            if command.lower() == "/confirm":
                self.execute_order(chat_id)
                return True
            elif command.lower() == "/cancel":
                self.cancel_order(chat_id)
                return True
            else:
                self._send_message("❌ Используйте /confirm или /cancel")
                return True

        return False

    def start(self):
        if self._running:
            return

        self._running = True
        self._start_time = time.time()

        # Запускаем фоновый поток для отправки сообщений
        self._thread = threading.Thread(target=self._background_worker, daemon=True)
        self._thread.start()

        # Отправляем приветствие в Telegram
        telegram = _get_telegram()
        if telegram and telegram.enabled and telegram.chat_id:
            try:
                chat_id = int(telegram.chat_id)
                # Отправляем приветствие
                self._send_message(
                    "🤖 <b>Торговый бот запущен</b>\n\n"
                    "👇 Используйте меню внизу экрана для управления:"
                )
                # Отправляем меню
                self.send_reply_menu(chat_id)
            except Exception as e:
                print(f"⚠️ Ошибка отправки приветствия: {e}")

        success("✅ Telegram бот запущен")

    def _background_worker(self):
        """Фоновый поток для поддержания активности"""
        last_status = 0
        while self._running:
            try:
                now = time.time()
                # Отправляем статус раз в 10 минут
                if now - last_status >= 600:
                    last_status = now
                    if self.trading_bot:
                        self._cached_status_time = 0
                        self._send_message(self.get_status_text())
                time.sleep(60)
            except Exception:
                time.sleep(60)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        success("📱 Telegram интерфейс остановлен")


# ========== ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР ==========
telegram_bot = None


def init_telegram_bot(trading_bot=None):
    global telegram_bot
    telegram_bot = TelegramBot(trading_bot)
    return telegram_bot