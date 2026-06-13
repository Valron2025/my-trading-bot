"""Основной торговый цикл - сердце бота"""

import time
import threading
from datetime import datetime, time as dt_time
from typing import Optional

from ..config import config
from ..logger import info, success, error, warning, debug
from ..utils.time_utils import get_moscow_time


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


def _get_telegram():
    from trading_bot.telegram.telegram_notifier import get_telegram_notifier
    return get_telegram_notifier()


class TradingLoop:
    """Основной торговый цикл"""

    def __init__(self, bot):
        self.bot = bot
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._cycle_count = 0
        self._last_cycle_time = 0
        # Для оптимизации логирования
        self._last_short_status = None
        self._last_market_log_cycle = 0

    def start(self):
        """Запуск торгового цикла"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="TradingLoop")
        self._thread.start()
        success("🔄 Торговый цикл запущен")

    def stop(self):
        """Остановка торгового цикла"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        success("🛑 Торговый цикл остановлен")

    # ==================== ОСНОВНОЙ ЦИКЛ ====================

    def _run(self):
        """Основной цикл - с красивым логированием и полной обработкой ошибок"""
        info("🔄 TradingLoop запущен")

        # Синхронизация позиций при старте
        self._sync_positions()

        # ========== ПРИНУДИТЕЛЬНЫЙ ЗАПУСК ПЕРВОГО ЦИКЛА ==========
        info("🚀 ЗАПУСК ПЕРВОГО ТОРГОВОГО ЦИКЛА...")

        # Добавляем принудительную задержку перед первым циклом
        time.sleep(2)

        while self._running:
            try:
                cycle_start = time.time()
                self._cycle_count += 1

                # Очистка памяти каждые 50 циклов
                if self._cycle_count % 50 == 0:
                    self.cleanup_memory()

                # ========== КРАСИВОЕ РАЗДЕЛЕНИЕ ЦИКЛА ==========
                info(f"\n{'═' * 65}")
                info(f"🔄 ЦИКЛ #{self._cycle_count} | {datetime.now().strftime('%H:%M:%S')}")
                info(f"{'═' * 65}")

                # ========== 1. ПРОВЕРКА ВРЕМЕНИ ТОРГОВ ==========
                trading_allowed = self._is_trading_time()
                info(f"⏰ Торговля разрешена: {trading_allowed}")

                if not trading_allowed:
                    info(f"⏸️ Торговое время выключено, ждём 60 секунд")
                    time.sleep(60)
                    continue

                # ========== 2. ПОЛУЧЕНИЕ КАПИТАЛА ==========
                try:
                    available, total_capital, _ = _get_tbank().get_available_funds()
                    info(f"💰 КАПИТАЛ: {total_capital:.0f}₽ | СВОБОДНО: {available:.0f}₽")
                except Exception as e:
                    error(f"❌ Ошибка получения капитала: {e}")
                    time.sleep(10)
                    continue

                # ========== 3. АВТОМАТИЧЕСКАЯ НАСТРОЙКА SHORT ==========
                if total_capital > 0:
                    short_enabled, short_reason = config.update_short_settings(total_capital)

                    # Показываем статус SHORT только при изменении
                    if short_enabled != self._last_short_status:
                        if short_enabled:
                            info(f"🔻 SHORT АКТИВИРОВАН: {short_reason}")
                        else:
                            warning(f"🔻 SHORT ДЕАКТИВИРОВАН: {short_reason}")
                        self._last_short_status = short_enabled

                    # Получаем рыночные условия для адаптации (каждые 10 циклов)
                    if self._cycle_count - self._last_market_log_cycle >= 10:
                        try:
                            from trading_bot.analysis.market_analyzer import market_analyzer
                            market_conditions = market_analyzer.analyze_market_conditions()
                            config.update_market_conditions(
                                volatility=market_conditions.volatility,
                                trend=market_conditions.trend
                            )
                            vol_icon = "📈" if market_conditions.trend > 0 else "📉" if market_conditions.trend < 0 else "➡️"
                            info(f"{vol_icon} РЫНОК: волатильность={market_conditions.volatility * 100:.1f}%, "
                                 f"тренд={market_conditions.trend:.2f} | {market_conditions.trend_direction}")
                            self._last_market_log_cycle = self._cycle_count
                        except Exception as e:
                            debug(f"Не удалось получить рыночные условия: {e}")

                # ========== 4. ПОЗИЦИИ ==========
                positions = self.bot._get_positions()
                current_positions = len(positions)

                if current_positions > 0:
                    info(f"📈 ПОЗИЦИИ: {current_positions}/{config.max_positions}")
                    for pos in positions:
                        figi = pos.get('figi', 'unknown')
                        ticker = self.bot._get_ticker_by_figi(figi) or figi[:12]
                        qty = pos.get('quantity', 0)
                        avg = pos.get('avg_price', 0)
                        side = "SHORT" if qty < 0 else "LONG"
                        cur = _get_tbank().get_current_price(figi)

                        if cur:
                            if qty < 0:
                                pnl = (avg - cur) * abs(qty)
                            else:
                                pnl = (cur - avg) * qty
                            icon = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
                            pnl_color = "+" if pnl > 0 else ""
                            info(
                                f"   {icon} {side} {ticker}: {abs(qty)}шт | {avg:.2f}→{cur:.2f} | {pnl_color}{pnl:+.2f}₽")
                        else:
                            info(f"   ⚪ {side} {ticker}: {abs(qty)}шт | {avg:.2f}₽ (цена не получена)")
                else:
                    info(f"📈 ПОЗИЦИЙ: 0/{config.max_positions}")

                # ========== 5. ПРОВЕРКА МАРЖИ ==========
                margin_rate = self._check_margin()
                if margin_rate > 85:
                    error(f"🔥 КРИТИЧЕСКАЯ МАРЖА: {margin_rate:.1f}%!")
                    self.bot.emergency_close_all_positions()
                elif margin_rate > 70:
                    warning(f"⚠️ ВЫСОКАЯ МАРЖА: {margin_rate:.1f}%")
                elif margin_rate > 50:
                    info(f"📊 МАРЖА: {margin_rate:.1f}%")

                # ========== 6. ЭКСТРЕННОЕ ОТКЛЮЧЕНИЕ SHORT ==========
                if config.use_short:
                    loss_streak = self._get_consecutive_losses()
                    should_disable, disable_reason = config.emergency_disable_short(margin_rate, loss_streak)
                    if should_disable:
                        warning(f"🚨 SHORT ЭКСТРЕННО ОТКЛЮЧЁН: {disable_reason}")
                        config.use_short = False
                        config.short_score_threshold = -20

                # ========== 7. ЗАКРЫТИЕ ПОЗИЦИЙ ПРИ НЕОБХОДИМОСТИ ==========
                self._close_positions_if_needed(positions)

                # ========== 8. ОПРЕДЕЛЕНИЕ РЕЖИМА ТОРГОВ ==========
                self._update_trading_mode()

                # ========== 9. ОТКРЫТИЕ НОВЫХ ПОЗИЦИЙ ==========
                if current_positions < config.max_positions:
                    info(f"🔍 ПОИСК СИГНАЛОВ...")
                    self._open_positions(total_capital, available, current_positions)
                else:
                    info(f"⏸️ ЛИМИТ ПОЗИЦИЙ: {current_positions}/{config.max_positions}")

                # ========== 10. ПРОВЕРКА СТОП-ЛОССОВ И ТЕЙК-ПРОФИТОВ ==========
                self._check_positions()

                # ========== 11. ЛОГИРОВАНИЕ КОНФИГУРАЦИИ (каждые 10 циклов) ==========
                if self._cycle_count % 10 == 0:
                    info(f"\n{'─' * 45}")
                    info(f"⚙️ ТЕКУЩАЯ КОНФИГУРАЦИЯ")
                    info(
                        f"   🎯 TP: +{config.take_profit_pct}% | 🛑 SL: -{config.stop_loss_pct}% | 🔻 TS: {config.trailing_stop_pct}%")
                    info(f"   📊 Размер позиции: {config.adaptive_position_size_pct * 100:.0f}%")
                    info(f"   🔻 SHORT: {'✅ ВКЛЮЧЁН' if config.use_short else '❌ ВЫКЛЮЧЕН'}")
                    info(
                        f"   🎫 LONG порог: ≥ {config.long_score_threshold} | SHORT порог: ≤ {config.short_score_threshold}")
                    info(
                        f"   ⏰ Таймаут: {config.adaptive_timeout_minutes} мин | 🔄 Цикл: {config.adaptive_cycle_seconds} сек")
                    info(f"   🌙 OTC режим: {'✅ ДА' if config.is_otc_mode else '❌ НЕТ'}")
                    info(f"{'─' * 45}")

                # ========== 12. ПАУЗА ДО СЛЕДУЮЩЕГО ЦИКЛА ==========
                cycle_time = time.time() - cycle_start
                sleep_time = max(1, config.adaptive_cycle_seconds - cycle_time)

                info(f"⏳ ПАУЗА: {sleep_time:.1f} сек (цикл занял {cycle_time:.1f} сек)")
                time.sleep(sleep_time)

            except Exception as e:
                error(f"❌ ОШИБКА В ТОРГОВОМ ЦИКЛЕ: {e}")
                import traceback
                error(traceback.format_exc())
                time.sleep(10)

        info("🛑 TradingLoop остановлен")

    # ==================== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ====================

    def _get_consecutive_losses(self) -> int:
        """
        Получить количество убыточных сделок подряд

        Должен находиться в классе TradingLoop
        """
        try:
            # Проверяем, есть ли метод
            if not hasattr(self.bot, '_get_orders_history'):
                return 0

            orders = self.bot._get_orders_history(limit=20)
            if not orders:
                return 0

            losses = 0
            for order in orders:
                profit = order.get('profit', 0)
                if profit is not None and profit < 0:
                    losses += 1
                else:
                    break  # Прерываем при первой прибыльной
            return losses
        except Exception as e:
            debug(f"Ошибка при подсчёте убытков: {e}")
            return 0

    def _sync_positions(self):
        """Синхронизация позиций при старте"""
        try:
            from trading_bot.risk.position_manager import position_manager
            position_manager.sync_with_broker()
            info("📊 Позиции синхронизированы с брокером")
        except Exception as e:
            warning(f"Ошибка синхронизации позиций: {e}")

    def _is_trading_time(self) -> bool:
        """Проверка, можно ли торговать сейчас (биржевые + OTC)"""
        from ..utils.time_utils import get_moscow_time
        from datetime import time as dt_time

        now = get_moscow_time()
        current_time = now.time()
        weekday = now.weekday()
        is_weekend = weekday >= 5

        # ========== ОСНОВНАЯ СЕССИЯ (9:50 - 18:59) ==========
        MAIN_START = dt_time(9, 50)
        MAIN_END = dt_time(18, 59)

        if MAIN_START <= current_time <= MAIN_END:
            config.is_otc_mode = False
            info(f"🏛️ ОСНОВНАЯ СЕССИЯ: торговля разрешена")
            return True

        # ========== УТРЕННЯЯ СЕССИЯ (6:50 - 9:50) ==========
        MORNING_START = dt_time(6, 50)
        MORNING_END = dt_time(9, 50)

        if MORNING_START <= current_time < MORNING_END:
            config.is_otc_mode = False
            info(f"🌅 УТРЕННЯЯ СЕССИЯ: торговля разрешена")
            return True

        # ========== ВЕЧЕРНЯЯ СЕССИЯ (19:00 - 23:49) ==========
        EVENING_START = dt_time(19, 0, 1)
        EVENING_END = dt_time(23, 49, 59)

        if EVENING_START <= current_time <= EVENING_END:
            config.is_otc_mode = False
            info(f"🌙 ВЕЧЕРНЯЯ СЕССИЯ: торговля разрешена")
            return True

        # ========== ВЫХОДНЫЕ (10:00 - 18:59) ==========
        WEEKEND_START = dt_time(10, 0)
        WEEKEND_END = dt_time(18, 59)

        if is_weekend and WEEKEND_START <= current_time <= WEEKEND_END:
            config.is_otc_mode = True
            info(f"📊 ВЫХОДНЫЕ: OTC режим, торговля разрешена")
            return True

        # ========== OTC РЕЖИМ (внебиржевые часы) ==========
        OTC_START = dt_time(6, 50)
        OTC_END = dt_time(23, 49, 59)

        if OTC_START <= current_time <= OTC_END:
            config.is_otc_mode = True
            info(f"🌙 OTC РЕЖИМ: торговля разрешена")
            return True

        info(f"⏸️ Торговля запрещена: время {current_time.hour}:{current_time.minute}")
        return False

    def _is_exchange_time(self) -> bool:
        """Проверка биржевого времени"""
        from ..utils.time_utils import get_moscow_time
        from datetime import time as dt_time

        now = get_moscow_time()
        current_time = now.time()
        weekday = now.weekday()

        if weekday >= 5:
            return False

        MORNING_START = dt_time(6, 50)
        MORNING_END = dt_time(9, 49, 59)
        MAIN_START = dt_time(9, 50)
        MAIN_END = dt_time(18, 59, 59)
        EVENING_START = dt_time(19, 0, 1)
        EVENING_END = dt_time(23, 49, 59)

        if MORNING_START <= current_time <= MORNING_END:
            return True
        if MAIN_START <= current_time <= MAIN_END:
            return True
        if EVENING_START <= current_time <= EVENING_END:
            return True

        return False

    def _is_holiday(self) -> bool:
        """Проверка праздничных дней 2026"""
        from ..utils.time_utils import get_moscow_time

        now = get_moscow_time()
        month, day = now.month, now.day

        holidays_2026 = [
            (1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7), (1, 8),
            (2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4),
        ]

        return (month, day) in holidays_2026

    def _adaptive_configuration(self, total_capital: float):
        """
        Адаптивная настройка под капитал

        Должен находиться в классе TradingLoop
        """
        mode = config.get_capital_mode(total_capital)

        # Обновляем параметры (используем адаптивные методы config)
        config.long_score_threshold = config.get_adaptive_long_threshold(total_capital)

        # ❌ НЕ УПРАВЛЯЕМ SHORT ЗДЕСЬ (это делает update_short_settings)
        # config.use_short = mode.get('use_short', False)  # УБРАТЬ!

        # Адаптивный размер позиции
        config.adaptive_position_size_pct = config.get_adaptive_position_size(total_capital)

        # Адаптивный стоп-лосс
        config.stop_loss_pct = config.get_adaptive_stop_loss(total_capital)

        # Адаптивный тейк-профит
        config.take_profit_pct = config.get_adaptive_take_profit(total_capital)

        # Адаптивный интервал цикла
        config.adaptive_cycle_seconds = config.get_adaptive_cycle(total_capital)

        # Адаптивная минимальная сумма сделки
        config.min_trade_amount = config.get_adaptive_min_trade_amount(total_capital)

        # Адаптивное количество позиций
        config.max_positions = config.get_adaptive_max_positions(total_capital)

        # ✅ Сохраняем текущий капитал в конфиг
        config.total_capital = total_capital

        info(f"⚙️ Адаптация: {mode.get('message', 'unknown')}, "
             f"позиций={config.max_positions}, "
             f"размер={config.adaptive_position_size_pct * 100:.1f}%, "
             f"SL={config.stop_loss_pct}%, TP={config.take_profit_pct}%, "
             f"SHORT={config.use_short}")

    def _update_trading_mode(self):
        """Автоматическое определение режима торгов (биржа или OTC)"""
        from ..utils.time_utils import get_moscow_time
        from datetime import time as dt_time

        now = get_moscow_time()
        current_time = now.time()
        weekday = now.weekday()

        # OTC режим: выходные или внебиржевые часы
        OTC_START = dt_time(6, 50)
        OTC_END = dt_time(23, 49, 59)

        is_weekend = weekday >= 5
        is_otc_hours = OTC_START <= current_time <= OTC_END

        # Определяем режим
        old_mode = config.is_otc_mode
        config.is_otc_mode = is_weekend or (is_otc_hours and not self._is_trading_time())

        # Если режим изменился, обновляем параметры фильтрации
        if old_mode != config.is_otc_mode:
            if config.is_otc_mode:
                info("🌙 ПЕРЕКЛЮЧЕНИЕ В OTC РЕЖИМ: применяем мягкие параметры фильтрации")
                self._apply_otc_parameters()
            else:
                info("🏛️ ПЕРЕКЛЮЧЕНИЕ В БИРЖЕВОЙ РЕЖИМ: применяем стандартные параметры")
                self._apply_exchange_parameters()

    def _apply_otc_parameters(self):
        """Применение параметров для OTC режима"""
        try:
            from trading_bot.analysis.instrument_filter import instrument_filter
            instrument_filter.min_avg_volume = config.otc_min_avg_volume
            instrument_filter.min_volume_ratio = config.otc_min_volume_ratio
            info(f"   📊 OTC параметры: мин.объём={config.otc_min_avg_volume}, мин.лота={config.otc_min_trade_amount}₽")
        except Exception as e:
            debug(f"Ошибка применения OTC параметров: {e}")

    def _apply_exchange_parameters(self):
        """Применение параметров для биржевого режима"""
        try:
            from trading_bot.analysis.instrument_filter import instrument_filter
            instrument_filter.min_avg_volume = config.exchange_min_avg_volume
            instrument_filter.min_volume_ratio = config.exchange_min_volume_ratio
            info(
                f"   📊 Биржевые параметры: мин.объём={config.exchange_min_avg_volume}, мин.лота={config.exchange_min_trade_amount}₽")
        except Exception as e:
            debug(f"Ошибка применения биржевых параметров: {e}")

    def _check_margin(self) -> float:
        """
        Проверка маржинального статуса

        Должен находиться в классе TradingLoop
        """
        try:
            margin_info = _get_tbank().get_margin_info()
            margin_rate = margin_info.get('margin_rate', 0) if margin_info else 0

            if margin_rate > 85:
                error(f"🔥 КРИТИЧЕСКАЯ МАРЖА: {margin_rate:.1f}%!")

                telegram = _get_telegram()
                if telegram:
                    telegram.send_error(f"🔥 КРИТИЧЕСКАЯ МАРЖА {margin_rate:.1f}%!")

                # Закрываем позиции
                self.bot.emergency_close_all_positions()

            elif margin_rate > 70:
                warning(f"⚠️ ВЫСОКАЯ МАРЖА: {margin_rate:.1f}%")

            return margin_rate or 0

        except Exception as e:
            debug(f"Ошибка проверки маржи: {e}")
            return 0

    def _close_positions_if_needed(self, positions):
        """Закрытие позиций при необходимости"""
        now = get_moscow_time()
        current_time = now.time()

        # Проверяем наличие position_closer
        if not hasattr(self.bot, 'position_closer') or not self.bot.position_closer:
            return

        # Основная сессия заканчивается в 18:59
        MAIN_END = dt_time(18, 59)

        # Если до конца сессии меньше 5 минут, закрываем все позиции
        if current_time >= dt_time(18, 54) and current_time <= MAIN_END:
            minutes_left = (dt_time(18, 59, 0).hour * 60 + dt_time(18, 59, 0).minute -
                            current_time.hour * 60 - current_time.minute)

            if minutes_left <= 5 and minutes_left > 0:
                self.bot.position_closer.close_all_positions_forced("main", minutes_left)

        # Вечерняя сессия заканчивается в 23:49
        EVENING_END = dt_time(23, 49, 59)

        if current_time >= dt_time(23, 44) and current_time <= EVENING_END:
            minutes_left = (dt_time(23, 49, 0).hour * 60 + dt_time(23, 49, 0).minute -
                            current_time.hour * 60 - current_time.minute)

            if minutes_left <= 5 and minutes_left > 0:
                self.bot.position_closer.close_all_positions_forced("evening", minutes_left)

    def _open_positions(self, total_capital: float, available_funds: float, current_positions: int):
        """Открытие новых позиций"""
        try:
            from trading_bot.api.tbank_client import tbank

            # Проверяем доступность рынка
            if hasattr(self.bot, '_get_figi_by_ticker'):
                test_figi = self.bot._get_figi_by_ticker("SBER")
                if test_figi:
                    is_available, status_msg = tbank.is_market_available(test_figi)
                    if not is_available:
                        info(f"⏸️ Рынок недоступен: {status_msg}")

            # Определяем текущую сессию
            now = get_moscow_time()
            current_time = now.time()
            MAIN_START = dt_time(9, 50)
            MAIN_END = dt_time(18, 59)

            if MAIN_START <= current_time <= MAIN_END:
                session = "main"
            else:
                session = "evening"

            info(
                f"🔍 ВЫЗОВ _find_and_open_positions: capital={total_capital:.2f}, available={available_funds:.2f}, positions={current_positions}, session={session}")

            # Открываем позиции
            self.bot._find_and_open_positions(
                total_capital=total_capital,
                available_funds=available_funds,
                current_positions=current_positions,
                minutes_left=0,
                session=session
            )

            info(f"✅ _find_and_open_positions завершён")

        except Exception as e:
            error(f"Ошибка открытия позиций: {e}")
            import traceback
            traceback.print_exc()

    def _check_positions(self):
        """Проверка стоп-лоссов и тейк-профитов"""
        try:
            from trading_bot.risk.position_manager import position_manager
            position_manager.check_all_positions()
        except Exception as e:
            debug(f"Ошибка проверки позиций: {e}")

    def _log_status(self, total_capital: float, positions_count: int, margin_rate: float):
        """Логирование статуса"""
        info(f"\n{'=' * 60}")
        info(f"📊 ТОРГОВЫЙ ЦИКЛ #{self._cycle_count}")
        info(f"   💰 Капитал: {total_capital:.2f}₽")
        info(f"   📈 Позиций: {positions_count}/{config.max_positions}")
        info(f"   📊 Маржа: {margin_rate:.1f}%")
        info(f"   🎯 TP: +{config.take_profit_pct}% | SL: -{config.stop_loss_pct}%")
        info(f"   🔻 SHORT: {'✅' if config.use_short else '❌'}")
        info(f"{'=' * 60}")

        # Обновляем метрики
        if hasattr(self.bot, 'metrics') and self.bot.metrics:
            self.bot.metrics.update_portfolio(total_capital, total_capital, positions_count)
            self.bot.metrics.update_margin_rate(margin_rate)
            self.bot.metrics.update_cycle_count(self._cycle_count)

    def _update_flask_status(self, total_capital: float):
        """Обновление статуса в Flask"""
        try:
            from app import update_bot_status
            positions = self.bot._get_positions()
            update_bot_status(
                running=True,
                cycle_count=self._cycle_count,
                positions=len(positions),
                capital=total_capital
            )
        except Exception:
            pass

    def cleanup_memory(self):
        """Принудительная очистка памяти"""
        import gc

        # Очищаем кэши TBankClient
        if hasattr(self.bot, 'tbank'):
            self.bot.tbank._candles_cache.clear()
            self.bot.tbank._shares_cache = None
            self.bot.tbank._ticker_cache.clear()
            self.bot.tbank._margin_cache = None

        # Очищаем кэш свечей
        try:
            from trading_bot.core.candle_builder import candle_builder
            candle_builder.clear_cache()
        except:
            pass

        # Запускаем сборщик мусора
        gc.collect()

        from trading_bot.logger import info
        info("🧹 Память очищена")