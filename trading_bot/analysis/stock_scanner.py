"""Сканер акций - поиск кандидатов для входа с полным анализом"""

import asyncio
import time
import os
from typing import List, Optional, Dict, Any
from datetime import datetime

from trading_bot.cache.cache_manager import TTLCache as UnifiedCache
USE_UNIFIED_CACHE = False
from ..config import config
from ..models import StockCandidate, StockAnalysis, OrderSide
from ..logger import info, success, error, warning, debug
from ..utils.time_utils import get_moscow_time

from trading_bot.cache.cache_manager import TTLCache, candles_cache


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
        self._scan_interval = int(os.environ.get('SCAN_INTERVAL', 60))  # ✅ 60 секунд
        self._cached_candidates = []
        self._low_liquidity_cache = {}

        # ПАРАМЕТРЫ ДЛЯ ОПТИМИЗАЦИИ
        self._scan_result_cache = {}  # {cache_key: {'candidates': [], 'timestamp': time}}
        self._scan_cache_ttl = int(os.environ.get('SCAN_CACHE_TTL', 120))  # ✅ 120 секунд
        self.max_tickers_to_scan = int(os.environ.get('MAX_TICKERS_TO_SCAN', 10))  # ✅ 10
        self.parallel_workers = int(os.environ.get('PARALLEL_WORKERS', 4))  # ✅ 4
        self._max_sequential = int(os.environ.get('MAX_SEQUENTIAL', 30))  # ✅ 30

        if USE_UNIFIED_CACHE:
            self._unified_cache = UnifiedCache(default_ttl=30, name="stock_scanner")

        self._candles_cache = TTLCache(default_ttl=120, max_size=500, name="scanner_candles")
        self._liquidity_cache = TTLCache(default_ttl=3600, max_size=500, name="liquidity_cache")
        self._price_cache_ttl = 10
        
        # ✅ АВТОМАТИЧЕСКИЙ РАСЧЁТ ПАРАМЕТРОВ
        self._calculate_optimal_params()

        info(f"📊 StockScanner: max_tickers={self.max_tickers_to_scan}, "
             f"workers={self.parallel_workers}, scan_interval={self._scan_interval}с")
        
    def _calculate_optimal_params(self):
        """Автоматический расчёт оптимальных параметров на основе системы"""
        import psutil
        import os
        
        # 1. Определяем мощность системы
        cpu_count = psutil.cpu_count() or 2
        mem = psutil.virtual_memory()
        total_ram_mb = mem.total / (1024 * 1024)
        
        # 2. Определяем тип окружения (Render/VPS/Local)
        is_render = os.environ.get('RENDER', False)
        is_vps = os.environ.get('VPS', False)
        
        # 3. Автоматический расчёт параметров
        if is_render:
            # Бесплатный Render: очень ограниченные ресурсы
            self.max_tickers_to_scan = min(3, cpu_count)
            self.parallel_workers = min(2, cpu_count)
            self._scan_interval = 120  # 2 минуты
            self._candles_cache_ttl = 300  # 5 минут
            self._scan_cache_ttl = 180  # 3 минуты
            self._min_api_interval = 2.0  # 2 секунды между запросами
            
        elif is_vps or cpu_count >= 4:
            # VPS или мощный компьютер
            self.max_tickers_to_scan = min(10, cpu_count * 2)
            self.parallel_workers = min(8, cpu_count)
            self._scan_interval = 60  # 1 минута
            self._candles_cache_ttl = 120  # 2 минуты
            self._scan_cache_ttl = 120  # 2 минуты
            self._min_api_interval = 0.5
            
        else:
            # Обычный компьютер
            self.max_tickers_to_scan = min(5, cpu_count)
            self.parallel_workers = min(4, cpu_count)
            self._scan_interval = 90  # 1.5 минуты
            self._candles_cache_ttl = 180  # 3 минуты
            self._scan_cache_ttl = 150  # 2.5 минуты
            self._min_api_interval = 1.0
            
        # 4. Расчёт на основе доступной памяти
        if total_ram_mb < 1024:  # < 1GB
            self.max_tickers_to_scan = min(self.max_tickers_to_scan, 3)
            self._candles_cache_ttl = 600  # 10 минут (меньше запросов)
            self._cache_max_size = 100
        elif total_ram_mb < 2048:  # < 2GB
            self.max_tickers_to_scan = min(self.max_tickers_to_scan, 5)
            self._cache_max_size = 200
        else:
            self._cache_max_size = 500
        
        # 5. Расчёт на основе задержек API (если есть данные)
        self._calculate_from_api_latency()
        
        info(f"📊 АВТО-ОПТИМИЗАЦИЯ: max_tickers={self.max_tickers_to_scan}, "
             f"workers={self.parallel_workers}, interval={self._scan_interval}с, "
             f"ram={total_ram_mb:.0f}MB, render={is_render}")
    
    def _calculate_from_api_latency(self):
        """Расчёт параметров на основе задержек API"""
        try:
            from trading_bot.api.tbank_client import api_monitor
            
            stats = api_monitor.get_stats()
            if not stats:
                return
            
            # Средняя задержка API
            avg_latency = 0
            count = 0
            for name, data in stats.items():
                if 'get_last_prices_batch' in name or 'get_candles' in name:
                    avg_latency += data['avg_ms']
                    count += 1
            
            if count > 0:
                avg_latency = avg_latency / count
                
                # Если API медленный (>1000ms) - уменьшаем нагрузку
                if avg_latency > 1000:
                    self.max_tickers_to_scan = min(self.max_tickers_to_scan, 3)
                    self._scan_interval = max(self._scan_interval, 180)
                    self.parallel_workers = min(self.parallel_workers, 2)
                    info(f"   ⚠️ API медленный ({avg_latency:.0f}ms), снижена нагрузка")
                    
        except Exception as e:
            debug(f"Не удалось получить статистику API: {e}")

    def _is_low_liquidity_ticker(self, ticker: str, figi: str = None) -> bool:
        """
        ПРОВЕРКА ЛИКВИДНОСТИ С КЭШИРОВАНИЕМ (ускоренная версия)
        """
        from trading_bot.cache import TTLCache
        from trading_bot.api.tbank_client import tbank

        ticker_upper = ticker.upper()

        cached = self._liquidity_cache.get(ticker_upper)
        if cached is not None:
            return cached

        try:
            if figi is None:
                figi = tbank._get_figi_by_ticker(ticker_upper)
            if not figi:
                self._liquidity_cache.set(ticker_upper, False)
                return False

            orderbook = tbank.get_orderbook(figi, depth=1)
            if orderbook:
                best_bid = orderbook.get('best_bid', 0)
                best_ask = orderbook.get('best_ask', 0)
                
                if best_bid > 0 and best_ask > 0:
                    spread_pct = (best_ask - best_bid) / best_bid * 100
                    if spread_pct > 0.5:
                        self._liquidity_cache.set(ticker_upper, True)
                        debug(f"📊 {ticker}: низкая ликвидность (спред {spread_pct:.2f}%)")
                        return True

            self._liquidity_cache.set(ticker_upper, False)
            return False

        except Exception as e:
            debug(f"⚠️ Ошибка проверки ликвидности {ticker}: {e}")
            self._liquidity_cache.set(ticker_upper, False)
            return False

    # ========== ОСНОВНОЙ МЕТОД SCAN ==========

    async def scan(
            self,
            available_funds: float,
            force_refresh: bool = False,
            use_parallel: bool = True,
            trading_loop=None
    ) -> List[StockCandidate]:
        """Сканирование рынка с улучшенным кэшированием"""

        cache_key = f"scan_{int(available_funds // 1000)}_{use_parallel}"

        if not force_refresh:
            cached = self._scan_result_cache.get(cache_key)
            if cached and (time.time() - cached['timestamp']) < self._scan_cache_ttl:
                debug(f"   📦 Результаты сканирования из кэша (актуальны {self._scan_cache_ttl} сек)")
                return cached['candidates'].copy() if cached['candidates'] else []

        now = time.time()
        if not force_refresh and now - self._last_scan_time < self._scan_interval:
            debug(f"⏸️ Используем кэш сканирования")
            return self._get_cached_candidates()

        info(f"🔍 Запуск сканирования рынка...")

        all_shares = _get_tbank().get_all_shares(limit=500)
        rub_shares = [s for s in all_shares if s.get('currency') == 'rub']

        if use_parallel and len(rub_shares) > 20:
            info(f"🚀 Используем ПАРАЛЛЕЛЬНЫЙ режим ({min(len(rub_shares), self.max_tickers_to_scan)} тикеров)")
            candidates = await self._scan_parallel(available_funds, limit=self.max_tickers_to_scan)
        else:
            info(f"📡 Используем ПОСЛЕДОВАТЕЛЬНЫЙ режим ({min(len(rub_shares), self._max_sequential)} тикеров)")
            candidates = await self._scan_sequential(available_funds, force_refresh, trading_loop)

        self._scan_result_cache[cache_key] = {
            'candidates': candidates.copy() if candidates else [],
            'timestamp': time.time()
        }

        self._cache_candidates(candidates)
        self._last_scan_time = now

        return candidates

    def clear_scan_cache(self):
        """Очистка кэша сканирования"""
        self._scan_result_cache.clear()
        self._last_scan_time = 0
        info("🧹 Кэш сканирования очищен")

    # ========== ПОСЛЕДОВАТЕЛЬНЫЙ АНАЛИЗ ==========

    async def _scan_sequential(
        self,
        available_funds: float,
        force_refresh: bool = False,
        trading_loop=None
    ) -> List[StockCandidate]:
        """ПОСЛЕДОВАТЕЛЬНЫЙ АНАЛИЗ С ОГРАНИЧЕНИЕМ"""

        all_shares = _get_tbank().get_all_shares(limit=500)
        rub_shares = [s for s in all_shares if s.get('currency') == 'rub']

        # ✅ ОГРАНИЧИВАЕМ КОЛИЧЕСТВО
        if len(rub_shares) > self.max_tickers_to_scan:
            rub_shares = rub_shares[:self.max_tickers_to_scan]
            info(f"📊 Ограничено до {self.max_tickers_to_scan} акций для сканирования")

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

            if lot_price < config.min_trade_amount:
                skipped_low_lot += 1
                continue

            if not self._check_instrument_filter(figi, ticker, current_price):
                skipped_filter += 1
                continue

            analysis = await self._get_combined_analysis(figi, ticker, current_price, trading_loop=trading_loop)

            if analysis is None:
                skipped_analysis_none += 1
                continue

            if analysis.score >= config.long_score_threshold:
                side = OrderSide.LONG
            elif analysis.score <= config.short_score_threshold and config.use_short:
                side = OrderSide.SHORT
            else:
                continue

            candidate = StockCandidate(
                figi=figi,
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

        candidates.sort(key=lambda x: x.rank_score, reverse=True)

        info(f"📊 Результаты сканирования:")
        info(f"   ✅ Обработано: {processed}")
        info(f"   ❌ Нет цены: {skipped_no_price}")
        info(f"   📦 Мелкий лот: {skipped_low_lot}")
        info(f"   🚫 Отфильтровано: {skipped_filter}")
        info(f"   ⚪ Анализ вернул None: {skipped_analysis_none}")
        info(f"   🚫 OTC пропущено: {skipped_otc}")
        info(f"   🎯 Кандидатов: {len(candidates)}")

        return candidates

    # ========== ПАРАЛЛЕЛЬНЫЙ АНАЛИЗ ==========

    async def _scan_parallel(
            self,
            available_funds: float,
            limit: int = 15,
            max_concurrent: int = 8
    ) -> List[StockCandidate]:
        """ПАРАЛЛЕЛЬНОЕ СКАНИРОВАНИЕ С ПРЕДВАРИТЕЛЬНОЙ ФИЛЬТРАЦИЕЙ"""
        from trading_bot.api.tbank_client import tbank
        import asyncio

        info(f"🚀 ПАРАЛЛЕЛЬНОЕ СКАНИРОВАНИЕ (макс {self.parallel_workers} потоков)")

        all_shares = tbank.get_all_shares(limit=500)
        rub_shares = [s for s in all_shares if s.get('currency') == 'rub']

        # ПРЕДВАРИТЕЛЬНАЯ ФИЛЬТРАЦИЯ
        filtered_shares = []
        for share in rub_shares:
            ticker = share.get('ticker')
            figi = share.get('figi')
            
            if not ticker or not figi:
                continue
                
            if tbank.is_confirmation_required(figi):
                continue
                
            if self._is_low_liquidity_ticker(ticker, figi):
                continue
                
            filtered_shares.append(share)
            
            if len(filtered_shares) >= self.max_tickers_to_scan:
                break

        info(f"📊 После фильтрации: {len(filtered_shares)} тикеров из {len(rub_shares)}")

        if not filtered_shares:
            return []

        semaphore = asyncio.Semaphore(self.parallel_workers)

        async def analyze_with_limit(share):
            async with semaphore:
                return await self._analyze_single_stock_async(share, available_funds)

        tasks = [analyze_with_limit(share) for share in filtered_shares]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        candidates = []
        for result in results:
            if result and not isinstance(result, Exception):
                candidates.append(result)
            elif isinstance(result, Exception):
                debug(f"   ⚠️ Ошибка: {str(result)[:80]}")

        candidates.sort(key=lambda x: x.rank_score, reverse=True)

        info(f"✅ ПАРАЛЛЕЛЬНОЕ СКАНИРОВАНИЕ: найдено {len(candidates)} кандидатов")

        return candidates[:5]

    async def _analyze_single_stock_async(
        self,
        share: Dict,
        available_funds: float
    ) -> Optional[StockCandidate]:
        """АНАЛИЗ ОДНОГО ТИКЕРА (асинхронная версия)"""
        from trading_bot.api.tbank_client import tbank
        from trading_bot.models import StockCandidate, OrderSide
        import asyncio

        ticker = share.get('ticker')
        figi = share.get('figi')
        lot = share.get('lot', 1)

        if not ticker or not figi:
            return None

        try:
            current_price = tbank.get_current_price(figi)
            if not current_price or current_price <= 0:
                return None

            if tbank.is_confirmation_required(figi):
                return None

            candles = await asyncio.to_thread(
                tbank.get_candles, figi, 3, 15
            )

            if not candles or len(candles) < 20:
                return None

            analysis = await asyncio.to_thread(
                self._run_technical_analysis,
                ticker, candles, current_price
            )

            if not analysis or analysis.get('score') == 0:
                return None

            score = analysis.get('score', 0)

            if score >= config.long_score_threshold:
                side = OrderSide.LONG
            elif score <= config.short_score_threshold and config.use_short:
                side = OrderSide.SHORT
            else:
                return None

            quantity = self._calculate_quick_quantity(available_funds, current_price, lot, score)

            if quantity <= 0:
                return None

            from ..models import StockAnalysis as StockAnalysisClass

            candidate = StockCandidate(
                figi=figi,
                name=ticker,
                ticker=ticker,
                price=current_price,
                lot=lot,
                lot_price=current_price * lot,
                analysis=StockAnalysisClass(
                    figi=figi,
                    name=ticker,
                    score=score,
                    buy_signal=(side == OrderSide.LONG),
                    sell_signal=(side == OrderSide.SHORT),
                    recommendation=f"{'BUY' if side == OrderSide.LONG else 'SHORT'} (parallel)",
                    signals=analysis.get('signals', [])[:5],
                    rsi=analysis.get('rsi', 50),
                    macd=analysis.get('macd', 0),
                    volume_ratio=analysis.get('volume_ratio', 1.0)
                ),
                side=side,
                rank_score=abs(score),
                quantity=quantity
            )

            return candidate

        except asyncio.TimeoutError:
            debug(f"⏰ Таймаут анализа {ticker}")
            return None
        except Exception as e:
            debug(f"❌ Ошибка анализа {ticker}: {e}")
            return None

    def _run_technical_analysis(
        self,
        ticker: str,
        candles: List,
        current_price: float
    ) -> Dict[str, Any]:
        """Синхронный технический анализ"""
        from trading_bot.analysis.technical_analyzer import analyzer
        return analyzer.analyze_with_candles(ticker, candles, current_price)

    def _calculate_quick_quantity(
        self,
        available_funds: float,
        price: float,
        lot: int,
        score: float
    ) -> int:
        """Быстрый расчёт размера позиции"""
        max_pct = 0.05 if abs(score) > 5 else 0.03
        max_amount = available_funds * max_pct

        quantity = int(max_amount / price)

        if lot > 1:
            quantity = (quantity // lot) * lot

        if quantity < lot:
            quantity = lot

        if quantity * price < 300:
            return 0

        return quantity

    # ========== ПОЛНЫЙ МЕТОД ПОИСКА И ОТКРЫТИЯ ==========

    async def find_and_open_positions(self, total_capital: float, available_funds: float,
                                      current_positions: int, minutes_left: int,
                                      session: str, min_auto_score: int = 0,
                                      trading_loop=None):
        """Поиск и открытие позиций"""
        from trading_bot.config import config
        from trading_bot.api.tbank_client import tbank
        from trading_bot.risk.position_manager import position_manager
        from trading_bot.core.settings_manager import settings_manager
        from trading_bot.analysis.fundamental_analyzer import fundamental_analyzer
        from trading_bot.analysis.news_sentiment import news_sentiment
        from trading_bot.analysis.technical_analyzer import analyzer
        import time

        # ========== ШАГ 1: СИНХРОНИЗАЦИЯ КАПИТАЛА ==========
        info(f"\n{'═' * 60}")
        info(f"🔍 [ШАГ 1/8] СИНХРОНИЗАЦИЯ КАПИТАЛА")
        info(f"{'═' * 60}")

        if config.total_capital != total_capital:
            config.total_capital = total_capital
            info(f"   ✅ Синхронизирован капитал: {total_capital:.2f}₽")
        else:
            info(f"   💰 Капитал: {total_capital:.2f}₽")

        # ========== ШАГ 2: ПРОВЕРКА МИНИМАЛЬНОГО ОСТАТКА ==========
        info(f"\n{'═' * 60}")
        info(f"🔍 [ШАГ 2/8] ПРОВЕРКА СРЕДСТВ")
        info(f"{'═' * 60}")

        MIN_RESERVE = 500
        info(f"   💵 Доступно средств: {available_funds:.2f}₽")
        info(f"   🔒 Минимальный резерв: {MIN_RESERVE}₽")

        if available_funds < MIN_RESERVE:
            warning(f"   ❌ КРИТИЧЕСКИ МАЛО СРЕДСТВ: {available_funds:.0f}₽ < {MIN_RESERVE}₽")
            return
        else:
            info(f"   ✅ Средств достаточно: {available_funds:.0f}₽ >= {MIN_RESERVE}₽")

        # ========== ШАГ 3: ОБНОВЛЕНИЕ НАСТРОЕК АНАЛИЗАТОРОВ ==========
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

        # ========== ШАГ 4: ПРОВЕРКА МАРЖИ ==========
        info(f"\n{'═' * 60}")
        info(f"🔍 [ШАГ 4/8] ПРОВЕРКА МАРЖИ")
        info(f"{'═' * 60}")

        margin_info = tbank.get_margin_info()
        margin_rate = margin_info.get('margin_rate', 0)
        info(f"   📊 Текущая маржа: {margin_rate:.1f}%")

        if margin_rate > 80:
            warning(f"   ❌ Высокая маржа ({margin_rate:.0f}%), пропускаем открытие")
            return
        elif margin_rate > 70:
            warning(f"   ⚠️ Маржа {margin_rate:.0f}% - осторожно")
        else:
            info(f"   ✅ Маржа в норме")

        # ========== ШАГ 5: ПОИСК КАНДИДАТОВ ==========
        info(f"\n{'═' * 60}")
        info(f"🔍 [ШАГ 5/8] ПОИСК КАНДИДАТОВ")
        info(f"{'═' * 60}")

        scan_start = time.time()
        candidates = await self.scan(available_funds, trading_loop=trading_loop)
        scan_time = time.time() - scan_start

        info(f"   ⏱ Время сканирования: {scan_time:.2f}с")
        info(f"   📋 Найдено кандидатов: {len(candidates)}")

        if not candidates:
            debug(f"   📭 Нет кандидатов для входа")
            return

        if candidates:
            info(f"\n   🏆 ТОП-5 КАНДИДАТОВ:")
            for i, c in enumerate(candidates[:5], 1):
                info(f"      {i}. {c.ticker}: score={c.analysis.score:.1f}, {c.side.value}, цена={c.price:.2f}₽")

        # ========== ШАГ 6: ФИЛЬТРАЦИЯ ПО SCORE ==========
        info(f"\n{'═' * 60}")
        info(f"🔍 [ШАГ 6/8] ФИЛЬТРАЦИЯ ПО SCORE")
        info(f"{'═' * 60}")

        info(f"   🎯 Мин. score для входа: {min_auto_score}")

        strong_candidates = [c for c in candidates if abs(c.analysis.score) >= min_auto_score]
        info(f"   📋 Кандидатов с score >= {min_auto_score}: {len(strong_candidates)}")

        if not strong_candidates:
            warning(f"   ⚠️ Нет кандидатов с score >= {min_auto_score}")
            if candidates:
                best_score = max(abs(c.analysis.score) for c in candidates)
                info(f"   💡 Максимальный score среди кандидатов: {best_score:.1f}")
            return

        # ========== ШАГ 7: ПРОВЕРКА БАЛАНСА ==========
        if trading_loop:
            info(f"\n{'═' * 60}")
            info(f"🔍 [ШАГ 7/8] ПРОВЕРКА БАЛАНСА ПОРТФЕЛЯ")
            info(f"{'═' * 60}")

            exposure = trading_loop.get_market_exposure()
            info(f"   ⚖️ Текущий баланс: LONG {exposure['long_pct'] * 100:.0f}% / SHORT {exposure['short_pct'] * 100:.0f}%")
            info(f"   📈 Всего позиций: {exposure['total_value']:.0f}₽")

            balanced_candidates = []
            for stock in strong_candidates:
                total_cost = stock.quantity * stock.price if stock.quantity > 0 else stock.lot_price

                info(f"\n   📊 Проверка {stock.ticker} ({stock.side.value}):")
                info(f"      Стоимость: {total_cost:.0f}₽")
                info(f"      Капитал: {total_capital:.0f}₽")

                can_open, reason = trading_loop.check_position_limits_advanced(
                    side=stock.side.value,
                    total_cost=total_cost,
                    total_capital=total_capital
                )

                if can_open:
                    info(f"      ✅ {stock.ticker}: ПРОШЁЛ проверку - {reason}")
                    balanced_candidates.append(stock)
                else:
                    info(f"      ❌ {stock.ticker}: НЕ ПРОШЁЛ - {reason}")

            if not balanced_candidates:
                info(f"\n   ⏸️ Нет кандидатов, прошедших проверку баланса")
                return

            strong_candidates = balanced_candidates
            info(f"\n   ✅ После проверки баланса осталось {len(strong_candidates)} кандидатов")
        else:
            info(f"\n{'═' * 60}")
            info(f"🔍 [ШАГ 7/8] ПРОВЕРКА БАЛАНСА (ПРОПУЩЕНА)")
            info(f"{'═' * 60}")
            info(f"   ⚠️ trading_loop не передан, проверка баланса пропущена")

        # ========== ШАГ 8: ОТКРЫТИЕ ПОЗИЦИЙ ==========
        info(f"\n{'═' * 60}")
        info(f"🔍 [ШАГ 8/8] ОТКРЫТИЕ ПОЗИЦИЙ")
        info(f"{'═' * 60}")

        strong_candidates.sort(key=lambda x: x.rank_score, reverse=True)
        max_new = config.max_positions - current_positions
        candidates_to_open = strong_candidates[:max_new]

        info(f"   📊 Текущих позиций: {current_positions}")
        info(f"   📈 Максимум позиций: {config.max_positions}")
        info(f"   🆕 Свободно мест: {max_new}")
        info(f"   🎯 Будет попытка открыть: {len(candidates_to_open)} позиций")

        if candidates_to_open:
            info(f"\n   📋 СПИСОК ДЛЯ ОТКРЫТИЯ:")
            for i, stock in enumerate(candidates_to_open, 1):
                info(f"      {i}. {stock.ticker}: score={stock.analysis.score:.1f}, {stock.side.value}, "
                     f"цена={stock.price:.2f}₽, лот={stock.lot}")

        opened = 0
        failed = 0

        for idx, stock in enumerate(candidates_to_open, 1):
            info(f"\n   {'─' * 50}")
            info(f"   📍 ОТКРЫТИЕ #{idx}/{len(candidates_to_open)}: {stock.ticker}")
            info(f"   {'─' * 50}")

            if position_manager.get_position(stock.figi):
                warning(f"      ⏸️ {stock.ticker}: позиция уже существует, пропускаем")
                continue

            info(f"      ✅ Позиции нет, можно открывать")

            quantity = self.bot._calculate_position_size(stock, available_funds, stock.analysis.score)

            if quantity <= 0:
                warning(f"      ⚠️ {stock.ticker}: размер позиции = {quantity}, пропускаем")
                failed += 1
                continue

            info(f"      ✅ Размер позиции: {quantity} шт")
            stock.quantity = quantity

            total_cost = quantity * stock.price
            info(f"      💰 Стоимость позиции: {total_cost:.2f}₽")
            info(f"      💵 Доступно средств: {available_funds:.2f}₽")

            if total_cost > available_funds * 0.95:
                warning(f"      ❌ Недостаточно средств: {total_cost:.0f}₽ > {available_funds:.0f}₽")
                failed += 1
                continue

            info(f"      ✅ Средств достаточно")

            if trading_loop:
                info(f"      ⚖️ Повторная проверка баланса...")
                can_open, reason = trading_loop.check_position_limits_advanced(
                    side=stock.side.value,
                    total_cost=total_cost,
                    total_capital=total_capital
                )
                if not can_open:
                    warning(f"      ❌ {stock.ticker}: {reason}")
                    failed += 1
                    continue
                info(f"      ✅ Баланс OK: {reason}")

            info(f"      🚀 Отправка заявки на открытие...")
            open_start = time.time()

            try:
                if stock.side == OrderSide.LONG:
                    info(f"         📈 LONG позиция, количество={quantity}")
                    success_flag = self.bot.position_opener.open_long_market(stock, quantity)
                else:
                    info(f"         📉 SHORT позиция, количество={quantity}")
                    success_flag = self.bot.position_opener.open_short_market(stock, quantity)

                open_time = time.time() - open_start

                if success_flag:
                    opened += 1
                    available_funds -= total_cost
                    info(f"\n      ✅ {stock.ticker}: ПОЗИЦИЯ УСПЕШНО ОТКРЫТА!")
                    info(f"         📊 Затрачено времени: {open_time:.2f}с")
                    info(f"         💰 Осталось средств: {available_funds:.2f}₽")
                    success(f"\n      ✅ {stock.ticker}: ПОЗИЦИЯ УСПЕШНО ОТКРЫТА!")
                else:
                    failed += 1
                    info(f"\n      ❌ {stock.ticker}: НЕ УДАЛОСЬ ОТКРЫТЬ ПОЗИЦИЮ!")
                    info(f"         ⏱ Время попытки: {open_time:.2f}с")
                    error(f"\n      ❌ {stock.ticker}: НЕ УДАЛОСЬ ОТКРЫТЬ ПОЗИЦИЮ!")

            except Exception as e:
                failed += 1
                info(f"\n      ❌ {stock.ticker}: ОШИБКА ПРИ ОТКРЫТИИ!")
                info(f"         Ошибка: {str(e)[:200]}")
                error(f"\n      ❌ {stock.ticker}: ОШИБКА ПРИ ОТКРЫТИИ!")
                error(f"         Ошибка: {str(e)[:200]}")
                import traceback
                debug(f"         {traceback.format_exc()}")

            if idx < len(candidates_to_open):
                time.sleep(0.5)

        info(f"\n{'═' * 60}")
        info(f"📊 ИТОГОВЫЙ ОТЧЁТ ОТКРЫТИЯ ПОЗИЦИЙ")
        info(f"{'═' * 60}")
        info(f"   ✅ Успешно открыто: {opened}")
        info(f"   ❌ Не удалось открыть: {failed}")
        info(f"   📋 Всего кандидатов: {len(candidates_to_open)}")

        if opened > 0:
            success(f"\n🎉 УСПЕШНО ОТКРЫТО {opened} НОВЫХ ПОЗИЦИЙ!")
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
            except Exception as e:
                debug(f"   ⚠️ Ошибка отправки Telegram: {e}")
        else:
            if failed > 0:
                warning(f"\n⚠️ НЕ УДАЛОСЬ ОТКРЫТЬ НИ ОДНОЙ ПОЗИЦИИ ({failed} попыток)")
            else:
                debug(f"\n📭 Нет позиций для открытия")

        info(f"{'═' * 60}\n")

    def _check_instrument_filter(self, figi: str, ticker: str, current_price: float) -> bool:
        """Проверка инструмента на соответствие фильтрам"""
        try:
            from trading_bot.analysis.instrument_filter import instrument_filter
            return instrument_filter.check_trading_quality(ticker)
        except Exception as e:
            debug(f"Ошибка фильтрации {ticker}: {e}")
            return True

    # ========== МЕТОДЫ РАБОТЫ СО СВЕЧАМИ С УЛУЧШЕННЫМ КЭШИРОВАНИЕМ ==========

    def _get_candles(self, figi: str) -> List:
        """Получение свечей с УЛУЧШЕННЫМ КЭШИРОВАНИЕМ"""
        from trading_bot.logger import debug, info, error
        from trading_bot.api.tbank_client import tbank
        import time

        try:
            debug(f"   🔍 _get_candles: figi={figi[:12]}...")

            cache_key = f"candles_5min_{figi}"
            cached = self._candles_cache.get(cache_key)
            
            if cached is not None:
                cache_age = time.time() - self._candles_cache._get_timestamp(cache_key)
                if cache_age < 30:
                    debug(f"   📦 СВЕЖИЙ КЭШ: {len(cached)} свечей (возраст {cache_age:.1f}с)")
                    return cached
                else:
                    debug(f"   ⏰ КЭШ УСТАРЕЛ ({cache_age:.1f}с), обновляем...")
                    
                    if hasattr(self.bot, '_background_tasks'):
                        asyncio.create_task(self._update_candles_background(figi, cache_key))
                        return cached

            info(f"   🔄 Запрос свечей для {figi[:12]}...")
            candles = tbank.get_candles(figi, days=2, interval_minutes=5)

            if not candles:
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

            if result:
                self._candles_cache.set(cache_key, result, ttl=120)
                debug(f"   💾 Сохранено в кэш: {len(result)} свечей (TTL=120с)")

            return result

        except Exception as e:
            error(f"❌ Ошибка получения свечей для {figi}: {e}")
            return []

    async def _update_candles_background(self, figi: str, cache_key: str):
        """Фоновое обновление кэша свечей"""
        try:
            from trading_bot.api.tbank_client import tbank
            
            await asyncio.sleep(0.5)
            
            candles = tbank.get_candles(figi, days=2, interval_minutes=5)
            if candles:
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
                
                if result:
                    self._candles_cache.set(cache_key, result, ttl=120)
                    debug(f"   🔄 Фоновое обновление: {len(result)} свечей для {figi[:12]}...")
        except Exception as e:
            debug(f"   ⚠️ Фоновое обновление не удалось: {e}")

    def _get_candles_15min(self, figi: str) -> List:
        """Получение 15-минутных свечей с улучшенным кэшированием"""
        from trading_bot.logger import info, error
        from trading_bot.api.tbank_client import tbank

        try:
            debug(f"   🔍 _get_candles_15min: figi={figi[:12]}...")

            cache_key = f"candles_15min_{figi}"
            cached = self._candles_cache.get(cache_key)
            if cached is not None:
                debug(f"   📦 Кэш: 15min свечи для {figi[:12]}... ({len(cached)} шт)")
                return cached

            candles = tbank.get_candles(figi, days=2, interval_minutes=15)

            if not candles or len(candles) < 20:
                debug(f"   ⚠️ Недостаточно 15min свечей: {len(candles) if candles else 0}/20")
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

            if result:
                self._candles_cache.set(cache_key, result, ttl=300)
                debug(f"   💾 Сохранено в кэш: {len(result)} 15min свечей (TTL=300с)")

            return result

        except Exception as e:
            info(f"   ❌ Ошибка получения 15min свечей: {e}")
            return []

    # ========== МЕТОДЫ КЭШИРОВАНИЯ КАНДИДАТОВ ==========

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
        self._scan_result_cache.clear()
        self._candles_cache.clear()
        self._liquidity_cache.clear()
        info("🧹 Весь кэш сканера очищен")

    # ========== БЫСТРЫЙ АНАЛИЗ ДЛЯ WEBSOCKET ==========

    async def quick_analyze_from_websocket(self, ticker: str, current_price: float) -> Optional[StockCandidate]:
        """Быстрый анализ для WebSocket/REST"""
        from trading_bot.analysis.technical_analyzer import analyzer
        from trading_bot.config import config
        from trading_bot.models import OrderSide, StockAnalysis as StockAnalysisModel

        try:
            figi = self.bot._get_figi_by_ticker(ticker)
            if not figi:
                return None

            candles = self._get_candles(figi)
            if not candles or len(candles) < 20:
                return None

            if hasattr(analyzer, 'analyze_with_candles'):
                technical = analyzer.analyze_with_candles(ticker, candles, current_price)
            else:
                return None

            if not technical:
                return None

            score = technical.get('score', 0)

            if score >= config.long_score_threshold:
                side = OrderSide.LONG
            elif score <= config.short_score_threshold and config.use_short:
                side = OrderSide.SHORT
            else:
                return None

            candidate = StockCandidate(
                figi=figi,
                name=ticker,
                ticker=ticker,
                price=current_price,
                lot=1,
                lot_price=current_price,
                analysis=StockAnalysisModel(
                    figi=figi,
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

    # ========== КОМБИНИРОВАННЫЙ АНАЛИЗ ==========

    async def _get_combined_analysis(self, figi: str, ticker: str, current_price: float,
                                     trading_loop=None) -> Optional[StockAnalysis]:
        """ПОЛНЫЙ КОМБИНИРОВАННЫЙ АНАЛИЗ"""
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

        if hasattr(fundamental_analyzer, 'clear_cache'):
            fundamental_analyzer.clear_cache(ticker)
            debug(f"   🧹 Очищен кэш FA для {ticker}")

        try:
            from trading_bot.core.candle_sync_wrapper import invalidate_cache_for_ticker
            invalidate_cache_for_ticker(ticker)
            debug(f"   🧹 Очищен MOEX кэш для {ticker}")
        except (ImportError, AttributeError):
            pass

        candles = self._get_candles(figi)
        info(f"   📊 Свечей получено: {len(candles) if candles else 0}")

        if candles and len(candles) > 0:
            first = candles[0]
            info(f"   🔍 формат свечей (5min) = {type(first).__name__}")
            if isinstance(first, dict):
                info(f"   ✅ Это словарь! Ключи: {list(first.keys())[:5]}")
                info(f"      close = {first.get('close', 'N/A')}")
            elif hasattr(first, 'close'):
                info(f"   ✅ Это объект! .close = {first.close}")

        min_candles = 15 if self._is_low_liquidity_ticker(ticker, figi) else 20

        if not candles or len(candles) < min_candles:
            debug(f"   ❌ {ticker}: недостаточно свечей ({len(candles) if candles else 0}/{min_candles})")
            return None

        debug(f"   ✅ Свечей: {len(candles)} (min={min_candles})")

        technical = None
        base_score = 0
        signals = []

        if settings_manager.get('technical_enabled', True):
            try:
                async with asyncio.timeout(8.0):
                    if hasattr(analyzer, 'analyze_with_candles'):
                        info(f"   📊 {ticker}: вызываем analyze_with_candles")
                        technical = analyzer.analyze_with_candles(ticker, candles, current_price, figi=figi)
                        info(f"   📊 {ticker}: результат score={technical.get('score') if technical else 'None'}")
                    else:
                        info(f"   ⚠️ {ticker}: analyze_with_candles не найден")
            except asyncio.TimeoutError:
                info(f"   ⏰ Таймаут тех.анализа для {ticker} (>8с)")
            except Exception as e:
                info(f"   ❌ Ошибка тех.анализа {ticker}: {e}")
                import traceback
                info(f"      {traceback.format_exc()[:200]}")

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
                        figi=figi,
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
                    debug(f"   ⚠️ {ticker}: недостаточно свечей для MTF ({len(candles_15min) if candles_15min else 0}/20)")
            except Exception as e:
                debug(f"   ❌ Ошибка MTF анализа {ticker}: {e}")

        if not technical:
            info(f"   ⚠️ {ticker}: технический анализ вернул None")
            return StockAnalysis(
                figi=figi, name=ticker, score=0, buy_signal=False, sell_signal=False,
                recommendation="НЕТ ДАННЫХ", signals=["Недостаточно данных для анализа"]
            )

        base_score = technical.get('score', 0)
        signals = technical.get('signals', [])
        info(f"   📊 Технический score: {base_score}, сигналов: {len(signals)}")

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
            figi=figi,
            name=ticker,
            score=final_score,
            buy_signal=buy_signal,
            sell_signal=sell_signal,
            recommendation=recommendation,
            signals=signals[:10],
            rsi=technical.get('rsi', 50),
            macd=technical.get('macd', 0),
            volume_ratio=technical.get('volume_ratio', 1.0),
            confidence=conf_level,
            take_profit_pct=technical.get('take_profit_pct', 1.2),
            stop_loss_pct=technical.get('stop_loss_pct', 0.6)
        )