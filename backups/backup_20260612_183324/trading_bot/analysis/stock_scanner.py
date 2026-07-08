"""Сканер акций - поиск кандидатов для входа с полным анализом"""

import asyncio
import time
from typing import List, Optional, Dict, Any
from datetime import datetime

from trading_bot.cache.cache_manager import TTLCache as UnifiedCache
USE_UNIFIED_CACHE = False
from ..config import config
from ..models import StockCandidate, StockAnalysis, OrderSide
from ..logger import info, success, error, warning, debug
from ..utils.time_utils import get_moscow_time


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


def _get_telegram():
    from trading_bot.telegram.telegram_notifier import get_telegram_notifier
    return get_telegram_notifier()


class StockScanner:
    """Сканер акций для поиска кандидатов на вход"""

    def __init__(self, bot):
        self.bot = bot
        self._analysis_cache = {}
        self._price_cache = {}
        self._last_scan_time = 0
        self._scan_interval = 30
        self._cached_candidates = []
        # ➕ ДОБАВЛЕНО: кэш для низколиквидных тикеров
        self._low_liquidity_cache = {}
        if USE_UNIFIED_CACHE:
            self._unified_cache = UnifiedCache(default_ttl=30, name="stock_scanner")

    # ➕ ДОБАВЛЕНО: метод для определения низколиквидных тикеров
    def _is_low_liquidity_ticker(self, ticker: str) -> bool:
        """Проверка, является ли тикер низколиквидным"""
        low_liquidity_tickers = {
            "OMZZP", "OMZZ", "KZOS", "YRSBP", "YRSB",
            "CNRU", "CNR", "BSPB", "BSP", "TUZA"
        }
        
        ticker_upper = ticker.upper()
        
        if ticker_upper in low_liquidity_tickers:
            if ticker_upper not in self._low_liquidity_cache:
                self._low_liquidity_cache[ticker_upper] = True
                debug(f"📊 {ticker}: низколиквидный тикер, min_candles=15")
            return True
        return False

    async def scan(self, available_funds: float, force_refresh: bool = False, trading_loop=None) -> List[StockCandidate]:
        """
        Сканирование рынка - поиск кандидатов для входа

        Args:
            available_funds: Доступные средства для торговли
            force_refresh: Принудительное обновление (игнорировать кэш)

        Returns:
            List[StockCandidate]: Список кандидатов, отсортированный по score
        """
        print("\n" + "=" * 80)
        print("🔥🔥🔥🔥🔥 SCAN МЕТОД ВЫЗВАН! 🔥🔥🔥🔥🔥")
        print(f"   available_funds = {available_funds}")
        print(f"   force_refresh = {force_refresh}")
        print("=" * 80 + "\n")

        # Проверка кэша (пропускаем если force_refresh=True)
        now = time.time()
        if not force_refresh and now - self._last_scan_time < self._scan_interval:
            debug(f"⏸️ Используем кэш сканирования (последнее сканирование {now - self._last_scan_time:.0f} сек назад)")
            return self._get_cached_candidates()

        info(f"🔍 Запуск сканирования рынка...")

        # Получаем список акций
        all_shares = _get_tbank().get_all_shares(limit=500)
        rub_shares = [s for s in all_shares if s.get('currency') == 'rub']

        if not rub_shares:
            warning("⚠️ Не удалось получить список акций")
            return []

        info(f"📊 Анализируем {len(rub_shares)} акций...")

        candidates = []
        processed = 0
        skipped_no_price = 0
        skipped_low_lot = 0
        skipped_filter = 0
        skipped_analysis_none = 0
        skipped_otc = 0

        for idx, share in enumerate(rub_shares):
            ticker = share.get('ticker', '')
            figi = share.get('figi', '')
            lot = share.get('lot', 1)
            name = share.get('name', '')

            if not figi or not ticker:
                continue

            # Получаем текущую цену
            current_price = _get_tbank().get_current_price(figi)

            if not current_price or current_price <= 0:
                skipped_no_price += 1
                continue

            lot_price = current_price * lot

            # Проверка минимальной суммы сделки
            if lot_price < config.min_trade_amount:
                skipped_low_lot += 1
                continue

            # Фильтрация по OTC и ликвидности
            if not self._check_instrument_filter(figi, ticker, current_price):
                skipped_filter += 1
                continue

            # Получаем анализ (технический + фундаментальный + новостной)
            analysis = await self._get_combined_analysis(figi, ticker, current_price, trading_loop=trading_loop)

            if analysis is None:
                skipped_analysis_none += 1
                continue

            # Проверка порогов для входа
            if analysis.score >= config.long_score_threshold:
                side = OrderSide.LONG
            elif analysis.score <= config.short_score_threshold and config.use_short:
                side = OrderSide.SHORT
            else:
                continue

            # Создаём кандидата
            candidate = StockCandidate(
                instrument_id=figi,
                name=name,
                ticker=ticker,
                price=current_price,
                lot=lot,
                lot_price=lot_price,
                analysis=analysis,
                side=side,
                rank_score=abs(analysis.score)
            )
            candidates.append(candidate)
            processed += 1

        # Сортируем по score
        candidates.sort(key=lambda x: x.rank_score, reverse=True)

        info(f"📊 Результаты сканирования:")
        info(f"   ✅ Обработано: {processed}")
        info(f"   ❌ Нет цены: {skipped_no_price}")
        info(f"   📦 Мелкий лот: {skipped_low_lot}")
        info(f"   🚫 Отфильтровано: {skipped_filter}")
        info(f"   ⚪ Анализ вернул None: {skipped_analysis_none}")
        info(f"   🚫 OTC пропущено: {skipped_otc}")
        info(f"   🎯 Кандидатов: {len(candidates)}")

        # Сохраняем в кэш
        self._cache_candidates(candidates)
        self._last_scan_time = now

        # 🔥 ДОБАВИТЬ ЭТОТ БЛОК ПЕРЕД ВЫВОДОМ КАНДИДАТОВ
        print(f"\n🔥🔥🔥 SCAN СТАТИСТИКА ПРОПУСКОВ:")
        print(f"   📊 Всего акций: {len(rub_shares)}")
        print(f"   ❌ Нет цены: {skipped_no_price}")
        print(f"   📦 Мелкий лот (<{config.min_trade_amount}₽): {skipped_low_lot}")
        print(f"   🚫 Отфильтровано (OTC/ликвидность): {skipped_filter}")
        print(f"   ⚪ Анализ вернул None: {skipped_analysis_none}")
        print(f"   🚫 OTC пропущено: {skipped_otc}")
        print(f"   ✅ Обработано: {processed}")
        print(f"   🎯 КАНДИДАТОВ: {len(candidates)}")

        print(f"\n🔥🔥🔥 SCAN РЕЗУЛЬТАТ: {len(candidates)} КАНДИДАТОВ")
        for c in candidates[:10]:
            print(f"   {c.ticker}: score={c.analysis.score}, side={c.side.value}, price={c.price}, lot={c.lot}")
        return candidates

    async def find_and_open_positions(self, total_capital: float, available_funds: float,
                                      current_positions: int, minutes_left: int,
                                      session: str, min_auto_score: int = 0,
                                      trading_loop=None):
        """
        Поиск и открытие позиций - полная логика с ПРОВЕРКОЙ БАЛАНСА И ДЕТАЛЬНЫМ ЛОГИРОВАНИЕМ
        """
        from trading_bot.config import config
        from trading_bot.api.tbank_client import tbank
        from trading_bot.risk.position_manager import position_manager
        from trading_bot.core.settings_manager import settings_manager
        from trading_bot.analysis.fundamental_analyzer import fundamental_analyzer
        from trading_bot.analysis.news_sentiment import news_sentiment
        from trading_bot.analysis.technical_analyzer import analyzer
        import time

        # ========== ПРИНУДИТЕЛЬНЫЙ PRINT ДЛЯ ДИАГНОСТИКИ ==========
        print("\n" + "=" * 80)
        print("🔥🔥🔥🔥🔥 find_and_open_positions ВЫЗВАН! 🔥🔥🔥🔥🔥")
        print(f"   total_capital = {total_capital}")
        print(f"   available_funds = {available_funds}")
        print(f"   current_positions = {current_positions}")
        print(f"   session = {session}")
        print(f"   min_auto_score = {min_auto_score}")
        print(f"   trading_loop = {trading_loop is not None}")
        print("=" * 80 + "\n")

        # ========== ШАГ 1: СИНХРОНИЗАЦИЯ КАПИТАЛА ==========
        print("📌 ШАГ 1/8: СИНХРОНИЗАЦИЯ КАПИТАЛА")
        info(f"\n{'═' * 60}")
        info(f"🔍 [ШАГ 1/8] СИНХРОНИЗАЦИЯ КАПИТАЛА")
        info(f"{'═' * 60}")

        if config.total_capital != total_capital:
            config.total_capital = total_capital
            info(f"   ✅ Синхронизирован капитал: {total_capital:.2f}₽")
            print(f"   ✅ Синхронизирован капитал: {total_capital:.2f}₽")
        else:
            info(f"   💰 Капитал: {total_capital:.2f}₽")
            print(f"   💰 Капитал: {total_capital:.2f}₽")

        # ========== ШАГ 2: ПРОВЕРКА МИНИМАЛЬНОГО ОСТАТКА ==========
        print("📌 ШАГ 2/8: ПРОВЕРКА СРЕДСТВ")
        info(f"\n{'═' * 60}")
        info(f"🔍 [ШАГ 2/8] ПРОВЕРКА СРЕДСТВ")
        info(f"{'═' * 60}")

        MIN_RESERVE = 500
        info(f"   💵 Доступно средств: {available_funds:.2f}₽")
        info(f"   🔒 Минимальный резерв: {MIN_RESERVE}₽")
        print(f"   💵 Доступно средств: {available_funds:.2f}₽, резерв: {MIN_RESERVE}₽")

        if available_funds < MIN_RESERVE:
            print(f"🔥🔥🔥 ВОЗВРАТ: недостаточно средств! {available_funds:.0f}₽ < {MIN_RESERVE}₽")
            warning(f"   ❌ КРИТИЧЕСКИ МАЛО СРЕДСТВ: {available_funds:.0f}₽ < {MIN_RESERVE}₽")
            return
        else:
            info(f"   ✅ Средств достаточно: {available_funds:.0f}₽ >= {MIN_RESERVE}₽")
            print(f"   ✅ Средств достаточно")

        # ========== ШАГ 3: ОБНОВЛЕНИЕ НАСТРОЕК АНАЛИЗАТОРОВ ==========
        print("📌 ШАГ 3/8: НАСТРОЙКИ АНАЛИЗАТОРОВ")
        info(f"\n{'═' * 60}")
        info(f"🔍 [ШАГ 3/8] НАСТРОЙКИ АНАЛИЗАТОРОВ")
        info(f"{'═' * 60}")

        fundamental_enabled = settings_manager.get('fundamental_enabled', True)
        news_enabled = settings_manager.get('news_enabled', True)
        technical_enabled = settings_manager.get('technical_enabled', True)

        fundamental_analyzer.enabled = fundamental_enabled
        news_sentiment.enabled = news_enabled
        analyzer.enabled = technical_enabled

        info(f"   📊 Фундаментальный анализ: {'✅ ВКЛ' if fundamental_enabled else '❌ ВЫКЛ'}")
        info(f"   📰 Новостной анализ: {'✅ ВКЛ' if news_enabled else '❌ ВЫКЛ'}")
        info(f"   📈 Технический анализ: {'✅ ВКЛ' if technical_enabled else '❌ ВЫКЛ'}")
        print(f"   📊 FA={fundamental_enabled}, News={news_enabled}, TA={technical_enabled}")

        # ========== ШАГ 4: ПРОВЕРКА МАРЖИ ==========
        print("📌 ШАГ 4/8: ПРОВЕРКА МАРЖИ")
        info(f"\n{'═' * 60}")
        info(f"🔍 [ШАГ 4/8] ПРОВЕРКА МАРЖИ")
        info(f"{'═' * 60}")

        margin_info = tbank.get_margin_info()
        margin_rate = margin_info.get('margin_rate', 0)
        info(f"   📊 Текущая маржа: {margin_rate:.1f}%")
        print(f"   📊 Текущая маржа: {margin_rate:.1f}%")

        if margin_rate > 80:
            print(f"🔥🔥🔥 ВОЗВРАТ: высокая маржа {margin_rate:.1f}% > 80%")
            warning(f"   ❌ Высокая маржа ({margin_rate:.0f}%), пропускаем открытие")
            return
        elif margin_rate > 70:
            warning(f"   ⚠️ Маржа {margin_rate:.0f}% - осторожно")
            print(f"   ⚠️ Маржа {margin_rate:.0f}% - осторожно, но продолжаем")
        else:
            info(f"   ✅ Маржа в норме")
            print(f"   ✅ Маржа в норме")

        # ========== ШАГ 5: ПОИСК КАНДИДАТОВ ==========
        print("📌 ШАГ 5/8: ПОИСК КАНДИДАТОВ")
        info(f"\n{'═' * 60}")
        info(f"🔍 [ШАГ 5/8] ПОИСК КАНДИДАТОВ")
        info(f"{'═' * 60}")

        scan_start = time.time()
        print("   🔄 Запуск сканирования рынка...")
        candidates = await self.scan(available_funds, trading_loop=trading_loop)
        scan_time = time.time() - scan_start

        info(f"   ⏱ Время сканирования: {scan_time:.2f}с")
        info(f"   📋 Найдено кандидатов: {len(candidates)}")
        print(f"   ⏱ Время сканирования: {scan_time:.2f}с, кандидатов: {len(candidates)}")

        if not candidates:
            print(f"🔥🔥🔥 ВОЗВРАТ: нет кандидатов!")
            debug(f"   📭 Нет кандидатов для входа")
            return

        # Показываем топ-5 кандидатов
        if candidates:
            info(f"\n   🏆 ТОП-5 КАНДИДАТОВ:")
            print(f"\n   🏆 ТОП-5 КАНДИДАТОВ:")
            for i, c in enumerate(candidates[:5], 1):
                info(f"      {i}. {c.ticker}: score={c.analysis.score:.1f}, {c.side.value}, цена={c.price:.2f}₽")
                print(f"      {i}. {c.ticker}: score={c.analysis.score:.1f}, {c.side.value}, цена={c.price:.2f}₽")

        # ========== ШАГ 6: ФИЛЬТРАЦИЯ ПО SCORE ==========
        print("📌 ШАГ 6/8: ФИЛЬТРАЦИЯ ПО SCORE")
        info(f"\n{'═' * 60}")
        info(f"🔍 [ШАГ 6/8] ФИЛЬТРАЦИЯ ПО SCORE")
        info(f"{'═' * 60}")

        info(f"   🎯 Мин. score для входа: {min_auto_score}")
        print(f"   🎯 Мин. score для входа: {min_auto_score}")

        strong_candidates = [c for c in candidates if abs(c.analysis.score) >= min_auto_score]
        print(f"\n🔥🔥🔥 STRONG_CANDIDATES: {len(strong_candidates)} из {len(candidates)}")
        print(f"   min_auto_score = {min_auto_score}")
        for c in strong_candidates[:10]:
            print(f"   {c.ticker}: score={c.analysis.score}, abs_score={abs(c.analysis.score)}")
        info(f"   📋 Кандидатов с score >= {min_auto_score}: {len(strong_candidates)}")
        print(f"   📋 Кандидатов с score >= {min_auto_score}: {len(strong_candidates)}")

        if not strong_candidates:
            print(f"🔥🔥🔥 ВОЗВРАТ: нет кандидатов с score >= {min_auto_score}")
            warning(f"   ⚠️ Нет кандидатов с score >= {min_auto_score}")
            if candidates:
                best_score = max(abs(c.analysis.score) for c in candidates)
                info(f"   💡 Максимальный score среди кандидатов: {best_score:.1f}")
                print(f"   💡 Максимальный score среди кандидатов: {best_score:.1f}")
            return

        # ========== ШАГ 7: ПРОВЕРКА БАЛАНСА (если есть trading_loop) ==========
        if trading_loop:
            print("📌 ШАГ 7/8: ПРОВЕРКА БАЛАНСА ПОРТФЕЛЯ")
            info(f"\n{'═' * 60}")
            info(f"🔍 [ШАГ 7/8] ПРОВЕРКА БАЛАНСА ПОРТФЕЛЯ")
            info(f"{'═' * 60}")

            # Получаем текущую экспозицию
            exposure = trading_loop.get_market_exposure()
            info(
                f"   ⚖️ Текущий баланс: LONG {exposure['long_pct'] * 100:.0f}% / SHORT {exposure['short_pct'] * 100:.0f}%")
            info(f"   📈 Всего позиций: {exposure['total_value']:.0f}₽")
            print(
                f"   ⚖️ Текущий баланс: LONG {exposure['long_pct'] * 100:.0f}% / SHORT {exposure['short_pct'] * 100:.0f}%")

            balanced_candidates = []
            for stock in strong_candidates:
                total_cost = stock.quantity * stock.price if stock.quantity > 0 else stock.lot_price

                info(f"\n   📊 Проверка {stock.ticker} ({stock.side.value}):")
                info(f"      Стоимость: {total_cost:.0f}₽")
                info(f"      Капитал: {total_capital:.0f}₽")
                print(f"\n   📊 Проверка {stock.ticker} ({stock.side.value}): стоимость={total_cost:.0f}₽")

                can_open, reason = trading_loop.check_position_limits_advanced(
                    side=stock.side.value,
                    total_cost=total_cost,
                    total_capital=total_capital
                )

                if can_open:
                    info(f"      ✅ {stock.ticker}: ПРОШЁЛ проверку - {reason}")
                    print(f"      ✅ {stock.ticker}: ПРОШЁЛ проверку - {reason}")
                    balanced_candidates.append(stock)
                else:
                    info(f"      ❌ {stock.ticker}: НЕ ПРОШЁЛ - {reason}")
                    print(f"      ❌ {stock.ticker}: НЕ ПРОШЁЛ - {reason}")

            if not balanced_candidates:
                print(f"🔥🔥🔥 ВОЗВРАТ: нет кандидатов, прошедших проверку баланса")
                info(f"\n   ⏸️ Нет кандидатов, прошедших проверку баланса")
                return

            strong_candidates = balanced_candidates
            print(f"\n🔥🔥🔥 ПОСЛЕ БАЛАНСИРОВКИ: {len(strong_candidates)} кандидатов")
            for c in strong_candidates[:10]:
                print(f"   {c.ticker}: side={c.side.value}, score={c.analysis.score}")
            info(f"\n   ✅ После проверки баланса осталось {len(strong_candidates)} кандидатов")
            print(f"\n   ✅ После проверки баланса осталось {len(strong_candidates)} кандидатов")
        else:
            print("📌 ШАГ 7/8: ПРОВЕРКА БАЛАНСА (ПРОПУЩЕНА) - trading_loop не передан")
            info(f"\n{'═' * 60}")
            info(f"🔍 [ШАГ 7/8] ПРОВЕРКА БАЛАНСА (ПРОПУЩЕНА)")
            info(f"{'═' * 60}")
            info(f"   ⚠️ trading_loop не передан, проверка баланса пропущена")

        # ========== ШАГ 8: ОТКРЫТИЕ ПОЗИЦИЙ ==========
        print("📌 ШАГ 8/8: ОТКРЫТИЕ ПОЗИЦИЙ")
        info(f"\n{'═' * 60}")
        info(f"🔍 [ШАГ 8/8] ОТКРЫТИЕ ПОЗИЦИЙ")
        info(f"{'═' * 60}")

        # Сортируем по score
        strong_candidates.sort(key=lambda x: x.rank_score, reverse=True)
        max_new = config.max_positions - current_positions
        candidates_to_open = strong_candidates[:max_new]
        print(f"\n🔥🔥🔥 КАНДИДАТЫ ДЛЯ ОТКРЫТИЯ: {len(candidates_to_open)}")
        print(f"   max_new = {max_new}, current_positions = {current_positions}, max_positions = {config.max_positions}")
        for c in candidates_to_open[:10]:
            print(f"   {c.ticker}: score={c.analysis.score}, side={c.side.value}, quantity={c.quantity if hasattr(c, 'quantity') else '?'}")

        info(f"   📊 Текущих позиций: {current_positions}")
        info(f"   📈 Максимум позиций: {config.max_positions}")
        info(f"   🆕 Свободно мест: {max_new}")
        info(f"   🎯 Будет попытка открыть: {len(candidates_to_open)} позиций")
        print(f"   📊 Текущих: {current_positions}, Максимум: {config.max_positions}, Свободно: {max_new}")
        print(f"   🎯 Будет попытка открыть: {len(candidates_to_open)} позиций")

        # Показываем список для открытия
        if candidates_to_open:
            info(f"\n   📋 СПИСОК ДЛЯ ОТКРЫТИЯ:")
            print(f"\n   📋 СПИСОК ДЛЯ ОТКРЫТИЯ:")
            for i, stock in enumerate(candidates_to_open, 1):
                info(f"      {i}. {stock.ticker}: score={stock.analysis.score:.1f}, {stock.side.value}, "
                     f"цена={stock.price:.2f}₽, лот={stock.lot}")
                print(f"      {i}. {stock.ticker}: score={stock.analysis.score:.1f}, {stock.side.value}, "
                      f"цена={stock.price:.2f}₽, лот={stock.lot}")

        opened = 0
        failed = 0

        for idx, stock in enumerate(candidates_to_open, 1):
            print(f"\n   {'─' * 50}")
            print(f"   📍 ОТКРЫТИЕ #{idx}/{len(candidates_to_open)}: {stock.ticker}")
            info(f"\n   {'─' * 50}")
            info(f"   📍 ОТКРЫТИЕ #{idx}/{len(candidates_to_open)}: {stock.ticker}")
            info(f"   {'─' * 50}")

            # Проверка: не открыта ли уже позиция
            print(f"      🔍 Проверка существующей позиции...")
            info(f"      🔍 Проверка существующей позиции...")
            if position_manager.get_position(stock.figi):
                print(f"      ⏸️ {stock.ticker}: позиция уже существует, пропускаем")
                warning(f"      ⏸️ {stock.ticker}: позиция уже существует, пропускаем")
                continue
            print(f"      ✅ Позиции нет, можно открывать")
            info(f"      ✅ Позиции нет, можно открывать")

            # Расчёт размера позиции
            print(f"      📐 Расчёт размера позиции...")
            info(f"      📐 Расчёт размера позиции...")
            quantity = self.bot._calculate_position_size(stock, available_funds, stock.analysis.score)

            if quantity <= 0:
                print(f"      ⚠️ {stock.ticker}: размер позиции = {quantity}, пропускаем")
                warning(f"      ⚠️ {stock.ticker}: размер позиции = {quantity}, пропускаем")
                failed += 1
                continue
            print(f"      ✅ Размер позиции: {quantity} шт")
            info(f"      ✅ Размер позиции: {quantity} шт")

            # Сохраняем количество в кандидате
            stock.quantity = quantity

            # Проверка достаточности средств
            total_cost = quantity * stock.price
            print(f"      💰 Стоимость позиции: {total_cost:.2f}₽")
            print(f"      💵 Доступно средств: {available_funds:.2f}₽")
            info(f"      💰 Стоимость позиции: {total_cost:.2f}₽")
            info(f"      💵 Доступно средств: {available_funds:.2f}₽")

            if total_cost > available_funds * 0.95:
                print(f"      ❌ Недостаточно средств: {total_cost:.0f}₽ > {available_funds:.0f}₽")
                warning(f"      ❌ Недостаточно средств: {total_cost:.0f}₽ > {available_funds:.0f}₽")
                failed += 1
                continue
            print(f"      ✅ Средств достаточно")
            info(f"      ✅ Средств достаточно")

            # Вторая проверка баланса (с реальным количеством)
            if trading_loop:
                print(f"      ⚖️ Повторная проверка баланса...")
                info(f"      ⚖️ Повторная проверка баланса...")
                can_open, reason = trading_loop.check_position_limits_advanced(
                    side=stock.side.value,
                    total_cost=total_cost,
                    total_capital=total_capital
                )
                if not can_open:
                    print(f"      ❌ {stock.ticker}: {reason}")
                    warning(f"      ❌ {stock.ticker}: {reason}")
                    failed += 1
                    continue
                print(f"      ✅ Баланс OK: {reason}")
                info(f"      ✅ Баланс OK: {reason}")

            # Открытие позиции
            print(f"      🚀 Отправка заявки на открытие...")
            info(f"      🚀 Отправка заявки на открытие...")
            open_start = time.time()

            try:
                if stock.side == OrderSide.LONG:
                    print(f"         📈 LONG позиция, количество={quantity}")
                    info(f"         📈 LONG позиция, количество={quantity}")
                    success_flag = self.bot.position_opener.open_long_market(stock, quantity)
                else:
                    print(f"         📉 SHORT позиция, количество={quantity}")
                    info(f"         📉 SHORT позиция, количество={quantity}")
                    success_flag = self.bot.position_opener.open_short_market(stock, quantity)

                open_time = time.time() - open_start

                if success_flag:
                    opened += 1
                    available_funds -= total_cost
                    print(f"\n      ✅ {stock.ticker}: ПОЗИЦИЯ УСПЕШНО ОТКРЫТА!")
                    print(f"         📊 Затрачено времени: {open_time:.2f}с")
                    print(f"         💰 Осталось средств: {available_funds:.2f}₽")
                    success(f"\n      ✅ {stock.ticker}: ПОЗИЦИЯ УСПЕШНО ОТКРЫТА!")
                    info(f"         📊 Затрачено времени: {open_time:.2f}с")
                    info(f"         💰 Осталось средств: {available_funds:.2f}₽")
                else:
                    failed += 1
                    print(f"\n      ❌ {stock.ticker}: НЕ УДАЛОСЬ ОТКРЫТЬ ПОЗИЦИЮ!")
                    print(f"         ⏱ Время попытки: {open_time:.2f}с")
                    error(f"\n      ❌ {stock.ticker}: НЕ УДАЛОСЬ ОТКРЫТЬ ПОЗИЦИЮ!")
                    info(f"         ⏱ Время попытки: {open_time:.2f}с")

            except Exception as e:
                failed += 1
                print(f"\n      ❌ {stock.ticker}: ОШИБКА ПРИ ОТКРЫТИИ!")
                print(f"         Ошибка: {str(e)[:200]}")
                error(f"\n      ❌ {stock.ticker}: ОШИБКА ПРИ ОТКРЫТИИ!")
                error(f"         Ошибка: {str(e)[:200]}")
                import traceback
                debug(f"         {traceback.format_exc()}")

            # Небольшая пауза между открытиями
            if idx < len(candidates_to_open):
                time.sleep(0.5)

        # ========== ИТОГОВЫЙ ОТЧЁТ ==========
        print(f"\n{'═' * 60}")
        print(f"📊 ИТОГОВЫЙ ОТЧЁТ ОТКРЫТИЯ ПОЗИЦИЙ")
        print(f"{'═' * 60}")
        print(f"   ✅ Успешно открыто: {opened}")
        print(f"   ❌ Не удалось открыть: {failed}")
        print(f"   📋 Всего кандидатов: {len(candidates_to_open)}")

        info(f"\n{'═' * 60}")
        info(f"📊 ИТОГОВЫЙ ОТЧЁТ ОТКРЫТИЯ ПОЗИЦИЙ")
        info(f"{'═' * 60}")
        info(f"   ✅ Успешно открыто: {opened}")
        info(f"   ❌ Не удалось открыть: {failed}")
        info(f"   📋 Всего кандидатов: {len(candidates_to_open)}")

        if opened > 0:
            print(f"\n🎉 УСПЕШНО ОТКРЫТО {opened} НОВЫХ ПОЗИЦИЙ!")
            success(f"\n🎉 УСПЕШНО ОТКРЫТО {opened} НОВЫХ ПОЗИЦИЙ!")

            # Отправляем уведомление в Telegram
            try:
                telegram = _get_telegram()
                if telegram:
                    positions_summary = []
                    for stock in candidates_to_open[:opened]:
                        if hasattr(stock, 'quantity') and stock.quantity > 0:
                            positions_summary.append(f"{stock.ticker}: {stock.quantity}шт по {stock.price:.2f}₽")

                    if positions_summary:
                        telegram.send_message(
                            f"🎉 **ОТКРЫТЫ НОВЫЕ ПОЗИЦИИ!**\n\n"
                            f"{chr(10).join(positions_summary)}\n\n"
                            f"📊 Капитал: {total_capital:.0f}₽\n"
                            f"💵 Свободно: {available_funds:.0f}₽"
                        )
                        print(f"   📱 Уведомление отправлено в Telegram")
            except Exception as e:
                debug(f"   ⚠️ Ошибка отправки Telegram: {e}")
                print(f"   ⚠️ Ошибка отправки Telegram: {e}")
        else:
            if failed > 0:
                print(f"\n⚠️ НЕ УДАЛОСЬ ОТКРЫТЬ НИ ОДНОЙ ПОЗИЦИИ ({failed} попыток)")
                warning(f"\n⚠️ НЕ УДАЛОСЬ ОТКРЫТЬ НИ ОДНОЙ ПОЗИЦИИ ({failed} попыток)")
            else:
                print(f"\n📭 Нет позиций для открытия")
                debug(f"\n📭 Нет позиций для открытия")

        print(f"{'═' * 60}\n")
        info(f"{'═' * 60}\n")

    def _check_instrument_filter(self, figi: str, ticker: str, current_price: float) -> bool:
        """Проверка инструмента на соответствие фильтрам"""
        try:
            from trading_bot.analysis.instrument_filter import instrument_filter
            return instrument_filter.check_trading_quality(ticker) # instrument_filter.check_instrument(figi, ticker, current_price)
        except Exception as e:
            debug(f"Ошибка фильтрации {ticker}: {e}")
            return True

    async def _get_combined_analysis(self, figi: str, ticker: str, current_price: float,
                                     trading_loop=None) -> Optional[StockAnalysis]:
        """
        ПОЛНЫЙ КОМБИНИРОВАННЫЙ АНАЛИЗ
        """
        from trading_bot.config import config
        from trading_bot.analysis.technical_analyzer import analyzer
        from trading_bot.analysis.fundamental_analyzer import fundamental_analyzer
        from trading_bot.analysis.news_sentiment import news_sentiment
        from trading_bot.core.settings_manager import settings_manager
        import time

        start_time = time.time()
        info(f"\n{'─' * 50}")
        info(f"🔬 АНАЛИЗ ТИКЕРА: {ticker}")
        info(f"{'─' * 50}")

        # Принудительно очищаем кэш фундаментальных данных для этого тикера
        if hasattr(fundamental_analyzer, 'clear_cache'):
            fundamental_analyzer.clear_cache(ticker)
            debug(f"   🧹 Очищен кэш FA для {ticker}")

        # Также очищаем кэш MOEX в candle_builder
        try:
            from trading_bot.core.candle_sync_wrapper import invalidate_cache_for_ticker
            invalidate_cache_for_ticker(ticker)
            debug(f"   🧹 Очищен MOEX кэш для {ticker}")
        except (ImportError, AttributeError):
            pass

        # 1. Получаем свечи
        candles = self._get_candles(figi)
        info(f"   📊 Свечей получено: {len(candles) if candles else 0}")

        # ДИАГНОСТИКА ФОРМАТА СВЕЧЕЙ
        if candles and len(candles) > 0:
            first = candles[0]
            info(f"   🔍 формат свечей (5min) = {type(first).__name__}")
            if isinstance(first, dict):
                info(f"   ✅ Это словарь! Ключи: {list(first.keys())[:5]}")
                info(f"      close = {first.get('close', 'N/A')}")
            elif hasattr(first, 'close'):
                info(f"   ✅ Это объект! .close = {first.close}")
            else:
                info(f"   ❌ НЕИЗВЕСТНЫЙ ФОРМАТ: {type(first)}")

        min_candles = 15 if self._is_low_liquidity_ticker(ticker) else 20

        if not candles or len(candles) < min_candles:
            debug(f"   ❌ {ticker}: недостаточно свечей ({len(candles) if candles else 0}/{min_candles})")
            return None

        debug(f"   ✅ Свечей: {len(candles)} (min={min_candles})")

        # ========== ТЕХНИЧЕСКИЙ АНАЛИЗ (обязательный) ==========
        technical = None
        base_score = 0
        signals = []

        if settings_manager.get('technical_enabled', True):
            try:
                async with asyncio.timeout(8.0):
                    # Используем analyze_with_candles с универсальной конвертацией
                    if hasattr(analyzer, 'analyze_with_candles'):
                        info(f"   📊 {ticker}: вызываем analyze_with_candles")
                        technical = analyzer.analyze_with_candles(ticker, candles, current_price)
                        info(f"   📊 {ticker}: результат score={technical.get('score') if technical else 'None'}")
                    else:
                        info(f"   ⚠️ {ticker}: analyze_with_candles не найден")
            except asyncio.TimeoutError:
                info(f"   ⏰ Таймаут тех.анализа для {ticker} (>8с)")
            except Exception as e:
                info(f"   ❌ Ошибка тех.анализа {ticker}: {e}")
                import traceback
                info(f"      {traceback.format_exc()[:200]}")

        # Если технический анализ не удался, но есть MTF анализ
        if not technical and trading_loop and hasattr(trading_loop, '_analyze_ticker_with_mtf'):
            try:
                debug(f"   📊 {ticker}: пробуем MTF анализ")
                candles_15min = self._get_candles_15min(figi)

                if candles_15min and len(candles_15min) > 0:
                    first = candles_15min[0]
                    info(f"   🔍 MTF свечи (15min) = {type(first).__name__}")

                if candles_15min and len(candles_15min) >= 20:
                    mtf_score, mtf_signals, mtf_details = await trading_loop._analyze_ticker_with_mtf(
                        ticker=ticker,
                        instrument_id=figi,
                        candles_15min=candles_15min,
                        total_capital=self.bot._last_capital if hasattr(self.bot, '_last_capital') else 100000
                    )
                    technical = {
                        'score': mtf_score,
                        'signals': mtf_signals,
                        'rsi': 50,
                        'macd': 0,
                        'volume_ratio': 1.0
                    }
                    base_score = mtf_score
                    signals = mtf_signals.copy()
                    if mtf_details:
                        debug(f"   📊 MTF {ticker}: {mtf_details.get('final', 'HOLD')}")
                else:
                    debug(
                        f"   ⚠️ {ticker}: недостаточно свечей для MTF ({len(candles_15min) if candles_15min else 0}/20)")
            except Exception as e:
                debug(f"   ❌ Ошибка MTF анализа {ticker}: {e}")

        if not technical:
            info(f"   ⚠️ {ticker}: технический анализ вернул None")
            return StockAnalysis(
                instrument_id=figi, name=ticker, score=0, buy_signal=False, sell_signal=False,
                recommendation="НЕТ ДАННЫХ", signals=["Недостаточно данных для анализа"]
            )

        base_score = technical.get('score', 0)
        signals = technical.get('signals', [])
        info(f"   📊 Технический score: {base_score}, сигналов: {len(signals)}")

        # ========== ФУНДАМЕНТАЛЬНЫЙ АНАЛИЗ ==========
        if settings_manager.get('fundamental_enabled', True) and fundamental_analyzer:
            try:
                async with asyncio.timeout(6.0):
                    if hasattr(fundamental_analyzer, 'enhance_technical_signal'):
                        enhanced_score, _, fund_data = await fundamental_analyzer.enhance_technical_signal(
                            ticker, base_score, signals
                        )
                        if fund_data:
                            fund_score = fund_data.get('score', 0) if isinstance(fund_data, dict) else 0
                            contribution = fund_score * 0.3
                            base_score += contribution
                            signals.append(f"📊 FA: {fund_score:+.1f} (вклад: {contribution:+.1f})")
                            info(f"   📊 Фундаментальный вклад: {contribution:+.1f}")
            except asyncio.TimeoutError:
                debug(f"   ⏰ Таймаут FA для {ticker} (>6с)")
            except Exception as e:
                debug(f"   ❌ Ошибка FA {ticker}: {e}")

        # ========== НОВОСТНОЙ АНАЛИЗ ==========
        if settings_manager.get('news_enabled', True) and news_sentiment and news_sentiment.enabled:
            try:
                async with asyncio.timeout(6.0):
                    if hasattr(news_sentiment, 'enhance_signal'):
                        enhanced_score, _, news_data = await news_sentiment.enhance_signal(ticker, base_score, signals)
                        if news_data:
                            news_impact = news_data.get('impact', 0)
                            if news_impact != 0:
                                base_score += news_impact
                                signals.append(f"📰 News: {news_impact:+.1f}")
                                info(f"   📰 Новостной вклад: {news_impact:+.1f}")
            except asyncio.TimeoutError:
                debug(f"   ⏰ Таймаут новостей для {ticker} (>6с)")
            except Exception as e:
                debug(f"   ❌ Ошибка новостей {ticker}: {e}")

        # ========== КОРРЕЛЯЦИОННЫЙ АНАЛИЗ ==========
        if settings_manager.get('correlation_analysis', False):
            try:
                from trading_bot.analysis.correlation_analyzer import correlation_analyzer
                from trading_bot.risk.position_manager import position_manager

                open_positions = []
                try:
                    positions = position_manager.get_all_positions()
                    open_positions = [pos.ticker for pos in positions.values() if hasattr(pos, 'ticker') and pos.ticker]
                except:
                    pass

                if open_positions:
                    corr_result = correlation_analyzer.analyze(ticker, open_positions)
                    if corr_result:
                        corr_penalty = corr_result.get('penalty', 0)
                        if corr_penalty > 0:
                            base_score -= corr_penalty * 0.5
                            signals.append(f"🔄 Корреляция: -{corr_penalty:.0%}")
                            info(f"   🔄 Корреляционный штраф: -{corr_penalty:.0%}")
            except Exception as e:
                debug(f"   ❌ Ошибка корреляции {ticker}: {e}")

        # ========== ИТОГОВЫЙ SCORE ==========
        final_score = max(-10, min(10, base_score))

        buy_signal = final_score >= config.long_score_threshold
        sell_signal = final_score <= config.short_score_threshold and config.use_short

        info(f"\n   {'─' * 40}")
        info(f"   📊 ИТОГОВЫЙ SCORE: {final_score:.1f}")
        info(f"   🎯 LONG порог: ≥ {config.long_score_threshold}")
        info(f"   🎯 SHORT порог: ≤ {config.short_score_threshold}")
        info(f"   🔻 SHORT разрешён: {config.use_short}")
        info(f"   {'─' * 40}")

        if buy_signal:
            recommendation = f"🟢 BUY (score={final_score:.1f})"
            success(f"🎯 {ticker}: СИГНАЛ НА ПОКУПКУ! score={final_score:.1f}")
        elif sell_signal:
            recommendation = f"🔴 SHORT (score={final_score:.1f})"
            success(f"🎯 {ticker}: СИГНАЛ НА SHORT! score={final_score:.1f}")
        else:
            recommendation = f"⚪ HOLD (score={final_score:.1f})"
            info(f"   ⚪ {ticker}: HOLD")

        elapsed = time.time() - start_time
        info(f"   ⏱ Общее время анализа: {elapsed:.2f}с")

        conf_level = min(0.95, 0.5 + abs(final_score) / 20)

        return StockAnalysis(
            instrument_id=figi,
            name=ticker,
            score=final_score,
            buy_signal=buy_signal,
            sell_signal=sell_signal,
            recommendation=recommendation,
            signals=signals[:10],
            rsi=technical.get('rsi', 50),
            macd=technical.get('macd', 0),
            volume_ratio=technical.get('volume_ratio', 1.0),
            confidence=conf_level
        )

    def _get_candles_15min(self, figi: str) -> List:
        """Получение 15-минутных свечей для MTF анализа - ВОЗВРАЩАЕТ СЛОВАРИ"""
        from trading_bot.logger import info, error
        from trading_bot.api.tbank_client import tbank

        try:
            info(f"   🔍 _get_candles_15min: figi={figi[:12]}...")

            candles = tbank.get_candles(figi, days=2, interval_minutes=15)
            info(f"   📊 Получено {len(candles) if candles else 0} 15min свечей")

            if not candles or len(candles) < 20:
                info(f"   ⚠️ Недостаточно 15min свечей: {len(candles) if candles else 0}/20")
                return []

            result = []
            for c in candles:
                if isinstance(c, (list, tuple)) and len(c) >= 2:
                    result.append({
                        'close': c[0],
                        'volume': c[1],
                        'high': c[0] * 1.005,
                        'low': c[0] * 0.995,
                        'open': c[0],
                    })
                elif hasattr(c, 'close'):
                    result.append({
                        'close': c.close,
                        'volume': getattr(c, 'volume', 0),
                        'high': getattr(c, 'high', c.close),
                        'low': getattr(c, 'low', c.close),
                        'open': getattr(c, 'open', c.close),
                    })
                elif isinstance(c, dict):
                    result.append(c)
                else:
                    info(f"   ⚠️ Неизвестный формат 15min свечи: {type(c)}")
                    result.append(c)

            info(f"   ✅ Возвращаем {len(result)} 15min свечей")
            if result:
                info(f"      Первая свеча: close={result[0].get('close', 'N/A')}")
            return result

        except Exception as e:
            info(f"   ❌ Ошибка получения 15min свечей: {e}")
            return []

    def _get_candles(self, figi: str) -> List:
        """Получение свечей для анализа - ВОЗВРАЩАЕТ СЛОВАРИ"""
        from trading_bot.logger import info, error
        from trading_bot.api.tbank_client import tbank

        try:
            info(f"   🔍 _get_candles: figi={figi[:12]}...")

            # ✅ ПРОПУСКАЕМ CandleBuilder (он асинхронный, а этот метод синхронный)
            # Используем только TBankClient напрямую
            info(f"   🔄 Используем TBankClient.get_candles()")
            candles = tbank.get_candles(figi, days=2, interval_minutes=5)
            info(f"   📊 TBankClient вернул {len(candles) if candles else 0} свечей")

            if not candles:
                info(f"   ❌ Нет свечей для {figi}")
                return []

            result = []
            for c in candles:
                if isinstance(c, (list, tuple)) and len(c) >= 2:
                    result.append({
                        'close': c[0],
                        'volume': c[1],
                        'high': c[0] * 1.005,
                        'low': c[0] * 0.995,
                        'open': c[0],
                    })
                elif isinstance(c, dict):
                    result.append(c)
                else:
                    result.append(c)

            info(f"   ✅ Возвращаем {len(result)} свечей")
            if result:
                info(f"      Первая свеча: close={result[0].get('close', 'N/A')}")
            return result

        except Exception as e:
            error(f"❌ Ошибка получения свечей для {figi}: {e}")
            return []

    def _get_cached_candidates(self) -> List[StockCandidate]:
        """Получение кэшированных кандидатов"""
        if hasattr(self, '_cached_candidates'):
            return self._cached_candidates
        return []

    def _cache_candidates(self, candidates: List[StockCandidate]):
        """Кэширование кандидатов"""
        self._cached_candidates = candidates

    def clear_cache(self):
        """Очистка кэша"""
        self._analysis_cache.clear()
        self._price_cache.clear()
        self._cached_candidates = []
        self._last_scan_time = 0
        info("🧹 Кэш сканера очищен")

    # ========== БЫСТРЫЙ АНАЛИЗ С ИСПОЛЬЗОВАНИЕМ WEBSOCKET ==========

    async def quick_analyze_from_websocket(self, ticker: str, current_price: float) -> Optional[StockCandidate]:
        """Быстрый анализ для WebSocket/REST"""
        from trading_bot.analysis.technical_analyzer import analyzer
        from trading_bot.config import config
        from trading_bot.models import OrderSide, StockAnalysis as StockAnalysisModel

        try:
            figi = self.bot._get_figi_by_ticker(ticker)
            if not figi:
                return None

            # Получаем свечи из кэша
            candles = self._get_candles(figi)
            if not candles or len(candles) < 20:
                return None

            # Быстрый технический анализ
            if hasattr(analyzer, 'analyze_with_candles'):
                technical = analyzer.analyze_with_candles(ticker, candles, current_price)
            else:
                return None

            if not technical:
                return None

            score = technical.get('score', 0)

            # Определяем сторону
            if score >= config.long_score_threshold:
                side = OrderSide.LONG
            elif score <= config.short_score_threshold and config.use_short:
                side = OrderSide.SHORT
            else:
                return None

            # Создаём кандидата
            candidate = StockCandidate(
                instrument_id=figi,
                name=ticker,
                ticker=ticker,
                price=current_price,
                lot=1,
                lot_price=current_price,
                analysis=StockAnalysisModel(
                    instrument_id=figi,
                    name=ticker,
                    score=score,
                    buy_signal=(side == OrderSide.LONG),
                    sell_signal=(side == OrderSide.SHORT),
                    recommendation=f"{'BUY' if side == OrderSide.LONG else 'SHORT'} (quick)",
                    signals=technical.get('signals', [])[:5],
                    rsi=technical.get('rsi', 50),
                    macd=technical.get('macd', 0),
                    volume_ratio=technical.get('volume_ratio', 1.0)
                ),
                side=side,
                rank_score=abs(score)
            )

            return candidate

        except Exception as e:
            debug(f"❌ Ошибка быстрого анализа {ticker}: {e}")
            return None
