"""Поиск и фильтрация акций для торговли"""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import List, Optional, Dict

from ..config import config
from ..models import StockCandidate, OrderSide, StockAnalysis
from ..logger import info, success, warning, debug

from .instrument_filter import instrument_filter


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


def _get_analyzer():
    from trading_bot.analysis.technical_analyzer import analyzer
    return analyzer


def _get_validator():
    from trading_bot.analysis.validator import validator
    return validator


def _get_instrument_filter():
    from trading_bot.analysis.instrument_filter import instrument_filter
    return instrument_filter


class StockScanner:
    """Поиск доступных акций с многопоточным анализом"""

    def __init__(self, bot):
        self.bot = bot
        self._stocks_cache = None
        self._stocks_cache_time = 0
        self._scanned_tickers = set()
        self._candidates_cache = []
        self._cache_ttl = 30

    def scan(self, available_funds: float, force_refresh: bool = False) -> List[StockCandidate]:
        """Автоматический поиск доступных акций"""
        from trading_bot.config import config

        info(f"🔍 StockScanner.scan() ВЫЗВАН, available_funds={available_funds:.2f}₽, force_refresh={force_refresh}")

        now = time.time()
        if (now - self._stocks_cache_time) < 30 and self._stocks_cache is not None:
            info(f"📦 Используем кэш сканера ({len(self._stocks_cache)} кандидатов)")
            return self._stocks_cache

        all_shares = _get_tbank().get_all_shares(limit=1000)
        info(f"📊 Получено {len(all_shares)} акций из API")

        try:
            _, total_capital, _ = _get_tbank().get_available_funds()
        except Exception:
            total_capital = available_funds

        is_short_disabled = total_capital < 2000

        if total_capital < 1000:
            info(f"⏸️ Капитал {total_capital:.0f}₽ < 1000₽ - торговля невозможна")
            return []

        # Динамические фильтры в зависимости от капитала
        if total_capital < 3000:
            max_lot_allowed = available_funds * 0.8
            min_price_filter = 1
            max_price_filter = 2000
            info(f"🟡 Микро-капитал ({total_capital:.0f}₽) - агрессивный поиск")
        elif total_capital < 5000:
            max_lot_allowed = available_funds * 0.7
            min_price_filter = 1
            max_price_filter = 2000
            info(f"🟡 Малый капитал ({total_capital:.0f}₽) - нормальный поиск")
        else:
            max_lot_allowed = available_funds * 0.7
            min_price_filter = config.min_share_price
            max_price_filter = config.max_share_price

        info("📊 ПОИСК АКЦИЙ (МНОГОПОТОЧНЫЙ)...")
        info(f"   Фильтры: цена [{min_price_filter}-{max_price_filter}], мин.лота={config.min_trade_amount}₽")

        fast_filtered = self._fast_filter(all_shares, max_lot_allowed, min_price_filter, max_price_filter)
        info(f"   Быстрая фильтрация: {len(fast_filtered)} акций для анализа")

        if not fast_filtered:
            warning("⚠️ НЕТ АКЦИЙ ПОСЛЕ БЫСТРОЙ ФИЛЬТРАЦИИ!")
            return []

        prices = self._get_prices_parallel(fast_filtered)
        info(f"   Цены получены: {len(prices)} акций")

        if not prices:
            warning("⚠️ НЕ УДАЛОСЬ ПОЛУЧИТЬ ЦЕНЫ!")
            return []

        candidates = self._analyze_parallel(fast_filtered, prices, is_short_disabled,
                                            min_price_filter, max_price_filter, max_lot_allowed)
        info(f"   Анализ завершён: {len(candidates)} кандидатов")

        # ========== ФИЛЬТРАЦИЯ С УЧЁТОМ РЕЖИМА (OTC/БИРЖА) ==========
        filtered_candidates = instrument_filter.filter_candidates(candidates, is_otc_mode=config.is_otc_mode)
        info(f"   После фильтрации ликвидности: {len(filtered_candidates)} кандидатов")

        # ✅ НОВЫЙ БЛОК: АВАРИЙНЫЙ РЕЖИМ ДЛЯ МАЛОГО КАПИТАЛА
        if not filtered_candidates and total_capital < 5000:
            info(f"\n🚨 АВАРИЙНЫЙ РЕЖИМ: нет кандидатов после фильтрации!")
            info(f"   Капитал: {total_capital:.0f}₽, ищем сильные LONG сигналы...")

            strong_buys = [c for c in candidates if c.side == OrderSide.LONG and c.analysis.score >= 2]

            if strong_buys:
                strong_buys.sort(key=lambda x: x.analysis.score, reverse=True)
                filtered_candidates = strong_buys[:2]
                info(f"   🚨 АВАРИЙНЫЙ РЕЖИМ: берём {len(filtered_candidates)} сильных LONG сигналов:")
                for cand in filtered_candidates:
                    info(f"      - {cand.name}: score={cand.analysis.score}, цена={cand.price:.2f}₽")
            else:
                any_long = [c for c in candidates if c.side == OrderSide.LONG]
                if any_long:
                    any_long.sort(key=lambda x: x.analysis.score, reverse=True)
                    filtered_candidates = any_long[:2]
                    info(f"   🚨 АВАРИЙНЫЙ РЕЖИМ: берём {len(filtered_candidates)} любых LONG сигналов")

        # ========== МЯГКАЯ ФИЛЬТРАЦИЯ КАЧЕСТВА ==========
        quality_candidates = []
        for cand in filtered_candidates:
            if cand.analysis.volume_ratio and cand.analysis.volume_ratio < 0.5:
                debug(f"   ⏭️ {cand.name}: очень низкий объём ({cand.analysis.volume_ratio:.1f}x) - пропуск")
                continue
            quality_candidates.append(cand)

        quality_candidates.sort(key=lambda x: x.rank_score, reverse=True)

        if quality_candidates:
            success(f"🏆 НАЙДЕНО КАНДИДАТОВ: {len(quality_candidates)}")
            for i, cand in enumerate(quality_candidates[:10], 1):
                side_icon = "🔴 SHORT" if cand.side == OrderSide.SHORT else "🟢 LONG"
                info(f"   {i}. {side_icon} {cand.name[:35]} | {cand.price:.2f}₽ | score: {cand.analysis.score}")
        else:
            warning("⚠️ НЕТ КАНДИДАТОВ ДЛЯ ОТКРЫТИЯ!")

        self._stocks_cache = quality_candidates
        self._stocks_cache_time = time.time()

        return quality_candidates

    def _fast_filter(self, all_shares, max_lot_allowed, min_price, max_price) -> List[Dict]:
        """Быстрая фильтрация акций"""

        filtered = []
        from trading_bot.api.tbank_client import tbank

        try:
            from trading_bot.risk.position_manager import position_manager
            current_pos_figis = position_manager.get_all_positions().keys()
        except ImportError:
            current_pos_figis = set()

        for stock_data in all_shares:
            # Базовые фильтры
            if stock_data.get('currency') != "rub":
                continue
            if not stock_data.get('api_trade_available', False):
                continue
            if stock_data['figi'] in current_pos_figis:
                continue

            # ✅ НОВАЯ ПРОВЕРКА: требует ли инструмент подтверждения сделок
            if tbank.is_confirmation_required(stock_data['figi']):
                debug(f"   ⏭️ {stock_data.get('ticker', 'unknown')}: требует подтверждения (30240) — пропускаем")
                continue

            # Временные блокировки
            try:
                from trading_bot.risk.position_manager import position_manager
                if position_manager.is_temp_blacklisted(stock_data['figi']):
                    continue
                if position_manager.is_temp_skipped(stock_data['figi']):
                    continue
            except ImportError:
                pass

            filtered.append(stock_data)

        return filtered[:150]

    def _get_prices_parallel(self, stocks: List[Dict]) -> Dict[str, float]:
        """Многопоточное получение цен"""
        prices = {}
        price_lock = Lock()

        def get_price(figi: str) -> tuple:
            try:
                price = _get_tbank().get_current_price(figi)
                return (figi, price)
            except Exception:
                return (figi, None)

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(get_price, s['figi']): s for s in stocks}
            for future in futures:
                try:
                    figi, price = future.result(timeout=10)
                    if price and price > 0:
                        with price_lock:
                            prices[figi] = price
                except Exception:
                    pass

        return prices

    def _analyze_parallel(self, stocks: List[Dict], prices: Dict[str, float],
                          is_short_disabled: bool, min_price: float, max_price: float,
                          max_lot_allowed: float) -> List[StockCandidate]:
        """Многопоточный анализ акций с проверкой качества"""
        import asyncio
        candidates = []
        candidates_lock = Lock()
        errors_lock = Lock()
        analysis_errors = 0

        async def analyze_one_async(stock_data: Dict) -> Optional[StockCandidate]:
            nonlocal analysis_errors
            figi = stock_data['figi']
            ticker = stock_data.get('ticker', '')
            price = prices.get(figi)

            if not price or price <= 0:
                return None

            if price < min_price or price > max_price:
                return None

            try:
                from trading_bot.analysis.technical_analyzer import analyzer
                analysis = await analyzer.analyze_stock(
                    figi=figi,
                    name=stock_data['name'],
                    ticker=ticker,
                    is_backtest=False
                )

                if analysis.recommendation == "HOLD (недостаточно данных)":
                    return None

                try:
                    _, total_capital, _ = _get_tbank().get_available_funds()
                except Exception:
                    total_capital = 0

                if total_capital < 5000:
                    LONG_THRESHOLD = 1
                    SHORT_THRESHOLD = -1
                else:
                    LONG_THRESHOLD = 2
                    SHORT_THRESHOLD = -2

                if analysis.score >= LONG_THRESHOLD:
                    side = OrderSide.LONG
                elif analysis.score <= SHORT_THRESHOLD:
                    if is_short_disabled:
                        return None
                    side = OrderSide.SHORT
                else:
                    return None

                rank_score = abs(analysis.score) * 10

                if analysis.rsi:
                    if side == OrderSide.LONG and analysis.rsi < 35:
                        rank_score += 25
                    elif side == OrderSide.SHORT and analysis.rsi > 65:
                        rank_score += 25

                if analysis.volume_ratio and analysis.volume_ratio > 1.5:
                    rank_score += 20
                elif analysis.volume_ratio and analysis.volume_ratio > 1.2:
                    rank_score += 10

                return StockCandidate(
                    figi=figi,
                    name=stock_data['name'],
                    price=price,
                    lot=stock_data['lot'],
                    lot_price=price * stock_data['lot'],
                    analysis=analysis,
                    side=side,
                    ticker=ticker,
                    rank_score=rank_score
                )
            except Exception as e:
                with errors_lock:
                    analysis_errors += 1
                    if analysis_errors <= 5:
                        debug(f"Ошибка анализа {stock_data.get('ticker', 'unknown')}: {e}")
                return None

        # ✅ ИСПРАВЛЕНО: loop определён до try
        def run_async(stock):
            loop = None
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                return loop.run_until_complete(analyze_one_async(stock))
            finally:
                if loop is not None:
                    loop.close()

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(run_async, stock): stock for stock in stocks}
            for future in futures:
                try:
                    result = future.result(timeout=15)
                    if result:
                        with candidates_lock:
                            candidates.append(result)
                except Exception:
                    pass

        return candidates

    def clear_cache(self):
        """Очистка кэша сканера"""
        self._candidates_cache = []
        self._scanned_tickers.clear()
        self._stocks_cache = None
        self._stocks_cache_time = 0
        info("🧹 Кэш сканера очищен")