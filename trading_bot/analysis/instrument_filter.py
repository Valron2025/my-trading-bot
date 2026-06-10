# trading_bot/analysis/instrument_filter.py
"""Фильтрация низколиквидных и OTC инструментов"""

import os
import asyncio
from typing import Dict, Any, Tuple, List
from datetime import datetime, timedelta, timezone

from trading_bot.logger import info, warning, debug, error
from trading_bot.config import config

# ✅ ПЕРЕМЕЩЁН ИМПОРТ СЮДА (был внутри метода)
try:
    from trading_bot.core.candle_sync_wrapper import get_candles_sync

    CANDLE_WRAPPER_AVAILABLE = True
except ImportError:
    CANDLE_WRAPPER_AVAILABLE = False
    debug("⚠️ CandleBuilder не доступен для instrument_filter")

MOSCOW_TZ = timezone(timedelta(hours=3))


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


def _get_candles_sync(ticker: str, days: int = 5) -> List[Tuple[float, float]]:
    """Отложенный импорт и получение свечей"""
    try:
        from trading_bot.core.candle_sync_wrapper import get_candles_sync
        return get_candles_sync(ticker, interval_minutes=5, days=days)
    except ImportError:
        debug(f"⚠️ CandleBuilder не доступен для {ticker}")
        return []


class InstrumentFilter:
    """
    Фильтр для исключения низколиквидных и OTC инструментов
    - OTC инструменты (внебиржевые)
    - Низкая ликвидность (объём торгов)
    - Инструменты, требующие подтверждения сделок
    - Широкий спред
    """

    def __init__(self):
        self.otc_cache: Dict[str, bool] = {}
        self.otc_cache_time: Dict[str, datetime] = {}
        self.cache_ttl = 3600  # 1 час

        # ========== БАЗОВЫЕ ПОРОГИ (будут перезаписываться динамически) ==========
        self.min_avg_volume = int(os.getenv('MIN_AVG_VOLUME', '50000'))
        self.min_volume_ratio = float(os.getenv('MIN_VOLUME_RATIO', '0.5'))
        self.max_spread_pct = float(os.getenv('MAX_SPREAD_PCT', '0.5'))
        self.min_daily_candles = int(os.getenv('MIN_DAILY_CANDLES', '10'))
        self.min_price = float(os.getenv('MIN_SHARE_PRICE', '1'))
        self.max_price = float(os.getenv('MAX_SHARE_PRICE', '2000'))
        self.max_margin_rate = float(os.getenv('MAX_MARGIN_RATE', '0.5'))
        self.max_candidates = int(os.getenv('MAX_CANDIDATES', '10'))

        # ========== OTC ПОРОГИ (для выходных и офф-часов) ==========
        self.otc_min_avg_volume = int(os.getenv('OTC_MIN_AVG_VOLUME', '5000'))
        self.otc_min_volume_ratio = float(os.getenv('OTC_MIN_VOLUME_RATIO', '0.3'))
        self.otc_min_trade_amount = int(os.getenv('OTC_MIN_TRADE_AMOUNT', '200'))

        # ========== БИРЖЕВЫЕ ПОРОГИ (для обычных торгов) ==========
        self.exchange_min_avg_volume = self.min_avg_volume
        self.exchange_min_volume_ratio = self.min_volume_ratio
        self.exchange_min_trade_amount = config.min_trade_amount

        # Чёрный список
        self.blacklist = self._load_blacklist()

        # Кэш для статуса заявок
        self._order_types_cache = None

        info(f"🔧 InstrumentFilter инициализирован")
        info(f"   📊 Биржевой режим: мин.объём={self.exchange_min_avg_volume:,}, мин.лота={self.exchange_min_trade_amount}₽")
        info(f"   🌙 OTC режим: мин.объём={self.otc_min_avg_volume:,}, мин.лота={self.otc_min_trade_amount}₽")
        info(f"   ⚡ Мин. соотношение объёма: {self.min_volume_ratio}x")
        info(f"   📈 Макс. спред: {self.max_spread_pct}%")
        info(f"   ⛔ Чёрный список: {len(self.blacklist)} тикеров (только из ENV)")

    def update_for_otc_mode(self, is_otc: bool):
        """Обновление параметров в зависимости от режима торгов"""
        if is_otc:
            self.min_avg_volume = self.otc_min_avg_volume
            self.min_volume_ratio = self.otc_min_volume_ratio
            config.min_trade_amount = self.otc_min_trade_amount
            debug(f"🌙 OTC режим: мин.объём={self.min_avg_volume}, мин.лота={self.otc_min_trade_amount}₽")
        else:
            self.min_avg_volume = self.exchange_min_avg_volume
            self.min_volume_ratio = self.exchange_min_volume_ratio
            config.min_trade_amount = self.exchange_min_trade_amount
            debug(f"🏛️ Биржевой режим: мин.объём={self.min_avg_volume}, мин.лота={self.exchange_min_trade_amount}₽")

    def _load_blacklist(self) -> set:
        """Загрузка чёрного списка из переменных окружения"""
        blacklist_str = os.getenv('INSTRUMENT_BLACKLIST', '')
        if blacklist_str:
            blacklist = set(ticker.strip().upper() for ticker in blacklist_str.split(','))
            if blacklist:
                info(f"   📋 Загружен чёрный список из ENV: {len(blacklist)} тикеров")
            return blacklist
        return set()

    def is_otc(self, figi: str) -> bool:
        """Проверка OTC инструмента"""
        if figi in self.otc_cache and figi in self.otc_cache_time:
            if datetime.now(MOSCOW_TZ) - self.otc_cache_time[figi] < timedelta(seconds=self.cache_ttl):
                return self.otc_cache[figi]

        try:
            tbank = _get_tbank()
            all_shares = tbank.get_all_shares(limit=1000)

            for stock in all_shares:
                if stock.get('figi') == figi:
                    exchange = stock.get('exchange', '')
                    is_qual = stock.get('for_qual_investor_flag', False)
                    is_otc = (exchange == 'INSTRUMENT_EXCHANGE_DEALER') or is_qual

                    from trading_bot.cache import TTLCache
                    if not hasattr(self, 'otc_cache_ttl'):
                        self.otc_cache_ttl = TTLCache(default_ttl=86400, max_size=1000, name="otc_cache")
                    self.otc_cache_ttl.set(figi, is_otc, ttl=86400)
                    self.otc_cache_time[figi] = datetime.now(MOSCOW_TZ)

                    if is_otc:
                        debug(f"🌙 OTC инструмент: {stock.get('ticker', figi[:8])}")
                    return is_otc
            return False
        except Exception as e:
            warning(f"Ошибка проверки OTC для {figi}: {e}")
            return False

    def is_liquid(self, ticker: str, price: float) -> bool:
        """Проверка ликвидности инструмента"""
        if price < self.min_price or price > self.max_price:
            return False
        if ticker in self.blacklist:
            return False
        return True

    def check_liquidity(self, ticker: str, candles: List[Tuple[float, float]]) -> Tuple[bool, str]:
        """Проверка ликвидности инструмента"""
        if not candles or len(candles) < 10:
            return False, f"недостаточно данных ({len(candles)} свечей)"

        volumes = [int(c[1]) for c in candles if c[1] > 0]
        if not volumes:
            return False, "нет данных об объёмах"

        avg_volume = sum(volumes) / len(volumes)

        if avg_volume < self.min_avg_volume:
            return False, f"средний объём {avg_volume:,.0f} < {self.min_avg_volume:,}"

        last_volume = volumes[-1] if volumes else 0
        if last_volume < avg_volume * self.min_volume_ratio:
            return False, f"последний объём {last_volume:,.0f} < {avg_volume:,.0f} * {self.min_volume_ratio:.1f}"

        return True, f"объём {avg_volume:,.0f}"

    def check_spread_by_figi(self, figi: str) -> Tuple[bool, float, str]:
        """Проверка спреда по FIGI"""
        try:
            tbank = _get_tbank()
            current_price = tbank.get_current_price(figi)
            if not current_price:
                return True, 0, "нет данных о цене"

            try:
                orderbook = tbank.get_orderbook(figi)
                if orderbook and orderbook.get('bids') and orderbook.get('asks'):
                    best_bid = orderbook['bids'][0]['price'] if orderbook['bids'] else 0
                    best_ask = orderbook['asks'][0]['price'] if orderbook['asks'] else 0
                    if best_bid > 0 and best_ask > 0:
                        spread_pct = (best_ask - best_bid) / current_price * 100
                    else:
                        spread_pct = 0.1
                else:
                    spread_pct = 0.1
            except Exception:
                spread_pct = 0.1

            if spread_pct > self.max_spread_pct:
                return False, spread_pct, f"спред {spread_pct:.2f}% > {self.max_spread_pct}%"
            return True, spread_pct, f"спред {spread_pct:.2f}%"
        except Exception as e:
            debug(f"Ошибка проверки спреда для {figi}: {e}")
            return True, 0, "ошибка проверки"

    def check_spread_by_ticker(self, ticker: str) -> Tuple[bool, float, str]:
        """Проверка спреда по тикеру"""
        try:
            tbank = _get_tbank()
            all_shares = tbank.get_all_shares(limit=500)
            for stock in all_shares:
                if stock.get('ticker') == ticker.upper():
                    figi = stock.get('figi')
                    if figi:
                        return self.check_spread_by_figi(figi)
            return True, 0, f"FIGI не найден для {ticker}"
        except Exception as e:
            debug(f"Ошибка проверки спреда для {ticker}: {e}")
            return True, 0, "ошибка проверки"

    def check_spread(self, identifier: str) -> Tuple[bool, float, str]:
        """Универсальная проверка спреда"""
        is_figi = identifier.startswith('BBG') or (len(identifier) > 10 and identifier.isdigit())
        if is_figi:
            return self.check_spread_by_figi(identifier)
        else:
            return self.check_spread_by_ticker(identifier)

    def check_trading_quality(self, ticker: str) -> Tuple[bool, str]:
        """Проверка качества торговли по тикеру"""
        ticker = ticker.upper()

        if ticker in self.blacklist:
            return False, "чёрный список"

        try:
            tbank = _get_tbank()
            all_shares = tbank.get_all_shares(limit=500)
            figi = None
            for stock in all_shares:
                if stock.get('ticker') == ticker:
                    figi = stock.get('figi')
                    break

            if figi and self.is_otc(figi):
                return False, "OTC инструмент"
        except Exception as e:
            debug(f"Ошибка проверки OTC для {ticker}: {e}")

        return True, "OK"

    # ========================================================================
    # ОСНОВНАЯ ФУНКЦИЯ ФИЛЬТРАЦИИ
    # ========================================================================

    def filter_candidates(self, candidates: List, is_otc_mode: bool = None) -> List:
        """Фильтрация списка кандидатов по ликвидности и качеству"""
        from trading_bot.config import config
        from trading_bot.logger import info

        if not candidates:
            return []

        if is_otc_mode is None:
            is_otc_mode = getattr(config, 'is_otc_mode', False)

        if config.total_capital < 15000:
            return self._filter_low_capital(candidates)
        if config.total_capital < 5000:
            return self._filter_micro_capital(candidates)

        return self._filter_normal(candidates, is_otc_mode)

    def _filter_low_capital(self, candidates: List) -> List:
        """Упрощённая фильтрация для малого капитала (до 15000₽)"""
        from trading_bot.api.tbank_client import tbank
        from trading_bot.logger import info, debug

        info(f"   💰 Малый капитал ({config.total_capital:.0f}₽) — УПРОЩАЕМ фильтрацию")

        filtered = []
        for cand in candidates:
            if cand.price <= 0 or cand.price > 50000:
                debug(f"   ⏭️ {cand.ticker}: цена вне диапазона ({cand.price:.2f}₽)")
                continue
            if tbank.is_confirmation_required(cand.figi):
                debug(f"   ⏭️ {cand.ticker}: требует подтверждения сделок")
                continue
            filtered.append(cand)

        if filtered:
            info(f"   ✅ После упрощённой фильтрации: {len(filtered)} кандидатов")
            return filtered
        else:
            info(f"   🚨 АВАРИЙНЫЙ РЕЖИМ: пропускаем всех без подтверждения")
            from trading_bot.api.tbank_client import tbank
            return [c for c in candidates if not tbank.is_confirmation_required(c.figi)]

    def _filter_micro_capital(self, candidates: List) -> List:
        """Микро-капитал (5000-15000₽) - пропускаем проверку ликвидности"""
        from trading_bot.logger import info

        info(f"   💰 Микро-капитал ({config.total_capital:.0f}₽) — пропускаем проверку ликвидности")
        filtered = [c for c in candidates if self.min_price <= c.price <= self.max_price]
        if filtered:
            return filtered
        return candidates[:5]

    def _filter_normal(self, candidates: List, is_otc_mode: bool) -> List:
        """Нормальная фильтрация для капитала > 15000₽"""
        from trading_bot.config import config
        from trading_bot.logger import info, debug
        from trading_bot.api.tbank_client import tbank

        old_min_volume = self.min_avg_volume
        old_min_ratio = self.min_volume_ratio
        old_min_trade = config.min_trade_amount

        stats = {'total': len(candidates), 'confirmation_required': 0, 'otc': 0,
                 'low_liquidity': 0, 'wide_spread': 0, 'price_out_of_range': 0,
                 'no_volume_data': 0, 'passed': 0}

        filtered = []

        try:
            self._apply_thresholds_by_mode(is_otc_mode)

            for cand in candidates:
                if not self._check_confirmation(cand, stats, tbank):
                    continue
                if not self._check_otc_status(cand, stats):
                    continue
                if not self._check_price_range(cand, stats):
                    continue
                if not self._check_liquidity(cand, stats):
                    continue
                if not self._check_spread(cand, stats):
                    continue

                stats['passed'] += 1
                filtered.append(cand)

        except Exception as e:
            error(f"❌ Ошибка в filter_candidates: {e}")
            import traceback
            error(traceback.format_exc())
        finally:
            self.min_avg_volume = old_min_volume
            self.min_volume_ratio = old_min_ratio
            config.min_trade_amount = old_min_trade

        self._log_filter_stats(stats, len(candidates))
        return filtered[:self.max_candidates]

    def _apply_thresholds_by_mode(self, is_otc_mode: bool):
        """Применение порогов фильтрации в зависимости от режима"""
        from trading_bot.config import config
        from trading_bot.logger import debug

        if is_otc_mode:
            if config.total_capital < 10000:
                self.min_avg_volume = min(self.otc_min_avg_volume, 2000)
                self.min_volume_ratio = max(self.otc_min_volume_ratio, 0.2)
                config.min_trade_amount = min(self.otc_min_trade_amount, 150)
                debug(f"🌙 OTC режим (малый капитал): мин.объём={self.min_avg_volume}, мин.лота={config.min_trade_amount}₽")
            else:
                self.min_avg_volume = self.otc_min_avg_volume
                self.min_volume_ratio = self.otc_min_volume_ratio
                config.min_trade_amount = self.otc_min_trade_amount
                debug(f"🌙 OTC режим: мин.объём={self.min_avg_volume}, мин.лота={config.min_trade_amount}₽")
        else:
            self.min_avg_volume = self.exchange_min_avg_volume
            self.min_volume_ratio = self.exchange_min_volume_ratio
            config.min_trade_amount = self.exchange_min_trade_amount
            debug(f"🏛️ Биржевой режим: мин.объём={self.min_avg_volume}, мин.лота={config.min_trade_amount}₽")

    def _check_confirmation(self, cand, stats, tbank) -> bool:
        if tbank.is_confirmation_required(cand.figi):
            stats['confirmation_required'] += 1
            debug(f"   ⏭️ {cand.ticker}: требует подтверждения сделок — пропускаем")
            return False
        return True

    def _check_otc_status(self, cand, stats) -> bool:
        if hasattr(self, 'is_otc') and self.is_otc(cand.figi):
            stats['otc'] += 1
            debug(f"   ⏭️ {cand.ticker}: OTC инструмент")
            return False
        return True

    def _check_price_range(self, cand, stats) -> bool:
        if cand.price <= 0 or cand.price > 50000:
            stats['price_out_of_range'] += 1
            debug(f"   ⏭️ {cand.ticker}: цена вне диапазона ({cand.price:.2f}₽)")
            return False
        return True

    def _check_liquidity(self, cand, stats) -> bool:
        from trading_bot.config import config

        if config.total_capital < 5000:
            return True

        try:
            if CANDLE_WRAPPER_AVAILABLE:
                candles = get_candles_sync(cand.ticker, interval_minutes=5, days=5)
            else:
                candles = []

            if candles and len(candles) >= 3:
                volumes = []
                for c in candles:
                    if isinstance(c, tuple) and len(c) >= 2:
                        volume = c[1] if c[1] is not None else 0
                        if volume > 0:
                            volumes.append(int(volume))
                    elif isinstance(c, dict):
                        volume = c.get('volume', 0)
                        if volume > 0:
                            volumes.append(int(volume))

                if volumes:
                    avg_volume = sum(volumes) / len(volumes)
                    threshold = self.min_avg_volume
                    if config.total_capital < 10000:
                        threshold = min(threshold, 1000)

                    if avg_volume < threshold:
                        stats['low_liquidity'] += 1
                        debug(f"   ⏭️ {cand.ticker}: низкая ликвидность ({avg_volume:,.0f} < {threshold:,})")
                        return False
                    else:
                        debug(f"   📊 {cand.ticker}: объём {avg_volume:,.0f} шт (OK)")
                        return True
                else:
                    stats['no_volume_data'] += 1
                    debug(f"   ⏭️ {cand.ticker}: нет данных об объёмах (volume=0)")
                    return False
            else:
                stats['no_volume_data'] += 1
                debug(f"   ⏭️ {cand.ticker}: недостаточно свечей ({len(candles) if candles else 0})")
                return False
        except Exception as e:
            debug(f"   ⏭️ {cand.ticker}: ошибка проверки ликвидности - {e}")
            return False

    def _check_spread(self, cand, stats) -> bool:
        from trading_bot.config import config

        if config.total_capital < 10000:
            return True

        if hasattr(self, 'check_spread') and callable(self.check_spread):
            try:
                spread_ok, spread_pct, _ = self.check_spread(cand.figi)
                if not spread_ok:
                    stats['wide_spread'] += 1
                    debug(f"   ⏭️ {cand.ticker}: широкий спред ({spread_pct:.2f}%)")
                    return False
            except Exception as e:
                debug(f"   ⏭️ {cand.ticker}: ошибка проверки спреда - {e}")
                return False
        return True

    def _log_filter_stats(self, stats: Dict, total_candidates: int):
        from trading_bot.logger import info

        if stats['passed'] < total_candidates:
            info(f"\n📊 ФИЛЬТРАЦИЯ ИНСТРУМЕНТОВ:")
            info(f"   📥 Всего: {stats['total']}")
            if stats['confirmation_required'] > 0:
                info(f"   🔐 Требуют подтверждения: {stats['confirmation_required']}")
            if stats['otc'] > 0:
                info(f"   🌙 OTC: {stats['otc']}")
            if stats['low_liquidity'] > 0:
                info(f"   💧 Низкая ликвидность: {stats['low_liquidity']}")
            if stats['no_volume_data'] > 0:
                info(f"   📊 Нет данных об объёмах: {stats['no_volume_data']}")
            if stats['wide_spread'] > 0:
                info(f"   📏 Широкий спред: {stats['wide_spread']}")
            if stats['price_out_of_range'] > 0:
                info(f"   💰 Цена вне диапазона: {stats['price_out_of_range']}")
            if stats['passed'] > 0:
                info(f"   ✅ Прошли: {stats['passed']}")
        else:
            if stats['passed'] > 0:
                info(f"✅ Все {stats['passed']} инструментов прошли фильтрацию")

    # ========================================================================
    # ДОПОЛНИТЕЛЬНЫЕ МЕТОДЫ
    # ========================================================================

    def check_liquidity_with_thresholds(self, ticker: str, candles: List[Tuple[float, float]],
                                        min_volume: int, min_ratio: float) -> Tuple[bool, str]:
        if not candles or len(candles) < 10:
            return False, f"недостаточно данных ({len(candles)} свечей)"

        volumes = [int(c[1]) for c in candles if c[1] > 0]
        if not volumes:
            return False, "нет данных об объёмах"

        avg_volume = sum(volumes) / len(volumes)

        if avg_volume < min_volume:
            return False, f"средний объём {avg_volume:,.0f} < {min_volume:,}"

        last_volume = volumes[-1] if volumes else 0
        if last_volume < avg_volume * min_ratio:
            return False, f"последний объём {last_volume:,.0f} < {avg_volume:,.0f} * {min_ratio:.1f}"

        return True, f"объём {avg_volume:,.0f}"

    def get_blocked_reason(self, ticker: str) -> str:
        ticker = ticker.upper()

        if ticker in self.blacklist:
            return "в чёрном списке"

        try:
            tbank = _get_tbank()
            all_shares = tbank.get_all_shares(limit=500)
            figi = None
            for stock in all_shares:
                if stock.get('ticker') == ticker:
                    figi = stock.get('figi')
                    break

            if figi and self.is_otc(figi):
                return "OTC инструмент"
        except Exception:
            pass

        return ""

    def get_stats(self) -> Dict[str, Any]:
        return {
            'min_avg_volume': self.min_avg_volume,
            'min_volume_ratio': self.min_volume_ratio,
            'max_spread_pct': self.max_spread_pct,
            'blacklist_size': len(self.blacklist),
            'otc_cache_size': len(self.otc_cache)
        }

    # ========================================================================
    # НОВЫЕ МЕТОДЫ ДЛЯ ПРОВЕРКИ ТИПОВ ЗАЯВОК (БЕЗ ХАРДКОДА)
    # ========================================================================

    def get_order_types_status(self, ticker: str) -> Dict[str, Any]:
        """
        ПОЛУЧЕНИЕ ПОЛНОГО СТАТУСА ДОСТУПНЫХ ТИПОВ ЗАЯВОК ДЛЯ ИНСТРУМЕНТА
        БЕЗ ХАРДКОДА - ТОЛЬКО ЧЕРЕЗ API С КЭШИРОВАНИЕМ

        Args:
            ticker: Тикер инструмента

        Returns:
            Dict с полями:
            - market_allowed: bool - доступны ли рыночные заявки
            - limit_allowed: bool - доступны ли лимитные заявки
            - stop_allowed: bool - доступны ли стоп-ордера
            - api_available: bool - доступна ли API торговля
            - is_otc: bool - является ли OTC инструментом
            - trading_status: str - описание статуса торгов
            - figi: str - FIGI инструмента
        """
        from trading_bot.logger import info, debug, warning, error
        from trading_bot.cache import TTLCache
        from trading_bot.api.tbank_client import tbank
        from datetime import datetime

        ticker_upper = ticker.upper()
        cache_key = f"order_types_status_{ticker_upper}"

        # ========== 1. ПРОВЕРКА КЭША (TTL 1 час) ==========
        if self._order_types_cache is None:
            self._order_types_cache = TTLCache(default_ttl=3600, max_size=500, name="order_types_cache")

        cached = self._order_types_cache.get(cache_key)
        if cached is not None:
            debug(f"📦 Кэш: статус заявок для {ticker} = {cached.get('market_allowed', '?')}")
            return cached

        # ========== 2. ИНИЦИАЛИЗАЦИЯ РЕЗУЛЬТАТА ==========
        result = {
            'ticker': ticker_upper,
            'figi': None,
            'market_allowed': True,
            'limit_allowed': True,
            'stop_allowed': False,
            'api_available': False,
            'is_otc': False,
            'trading_status': 'unknown',
            'timestamp': None
        }

        try:
            info(f"🔍 Получение статуса заявок для {ticker_upper}...")

            # ========== 3. ПОЛУЧАЕМ FIGI ==========
            figi = tbank._get_figi_by_ticker(ticker_upper)
            if not figi:
                warning(f"   ⚠️ FIGI для {ticker_upper} не найден")
                self._order_types_cache.set(cache_key, result, ttl=3600)
                return result

            result['figi'] = figi

            # ========== 4. ПОЛУЧАЕМ СТАТУС ТОРГОВ ==========
            status = tbank.get_trading_status(figi)

            result['api_available'] = status.get('api_trade_available', False)
            result['market_allowed'] = status.get('market_order_available', False)
            result['limit_allowed'] = status.get('limit_order_available', False)
            result['trading_status'] = status.get('trading_status_description', 'unknown')

            # ========== 5. ПРОВЕРЯЕМ OTC СТАТУС ==========
            try:
                result['is_otc'] = tbank.is_confirmation_required(figi)
            except Exception as e:
                debug(f"   ⚠️ Ошибка проверки OTC: {e}")
                result['is_otc'] = False

            # ========== 6. ПРОВЕРЯЕМ ПОДДЕРЖКУ СТОП-ОРДЕРОВ ==========
            if result['api_available'] and result['market_allowed']:
                try:
                    result['stop_allowed'] = tbank.supports_stop_orders(figi)
                except Exception as e:
                    debug(f"   ⚠️ Ошибка проверки стопов: {e}")
                    result['stop_allowed'] = False

            # ========== 7. ДЕТАЛЬНОЕ ЛОГИРОВАНИЕ ==========
            info(f"   📊 СТАТУС ЗАЯВОК ДЛЯ {ticker_upper}:")
            info(f"      🔌 API торговля: {'✅ ДОСТУПНА' if result['api_available'] else '❌ НЕ ДОСТУПНА'}")
            info(f"      🏷️ Рыночные заявки: {'✅ ДОСТУПНЫ' if result['market_allowed'] else '❌ НЕ ДОСТУПНЫ'}")
            info(f"      📋 Лимитные заявки: {'✅ ДОСТУПНЫ' if result['limit_allowed'] else '❌ НЕ ДОСТУПНЫ'}")
            info(f"      🛑 Стоп-ордера: {'✅ ПОДДЕРЖИВАЮТСЯ' if result['stop_allowed'] else '❌ НЕ ПОДДЕРЖИВАЮТСЯ'}")
            info(f"      🔐 OTC: {'✅ ДА' if result['is_otc'] else '❌ НЕТ'}")
            info(f"      📈 Статус торгов: {result['trading_status']}")

            # ========== 8. СОХРАНЯЕМ В КЭШ ==========
            result['timestamp'] = datetime.now().isoformat()
            self._order_types_cache.set(cache_key, result, ttl=3600)

            # ========== 9. ДОПОЛНИТЕЛЬНЫЕ РЕКОМЕНДАЦИИ ==========
            if not result['market_allowed']:
                info(f"   💡 Рекомендация: для {ticker_upper} использовать ТОЛЬКО лимитные заявки")

            if result['is_otc']:
                warning(f"   ⚠️ {ticker_upper} - OTC ИНСТРУМЕНТ! Требует подтверждения сделок")
                info(f"      🔧 Для торговли используйте только лимитные заявки")

            if not result['stop_allowed']:
                debug(f"   ℹ️ Для {ticker_upper} используется программный трейлинг-стоп")

        except Exception as e:
            error(f"   ❌ Ошибка получения статуса заявок для {ticker_upper}: {e}")
            result['error'] = str(e)[:100]

        return result

    def should_use_limit_order(self, ticker: str, side: str = None) -> Tuple[bool, str]:
        """
        ОПРЕДЕЛЕНИЕ, НУЖНО ЛИ ИСПОЛЬЗОВАТЬ ЛИМИТНУЮ ЗАЯВКУ

        Args:
            ticker: Тикер инструмента
            side: Сторона сделки ("BUY" или "SELL", опционально)

        Returns:
            Tuple[bool, str]: (использовать_лимитную, причина)
        """
        from trading_bot.utils.time_utils import is_pre_market_time, is_otc_trading_time
        from trading_bot.logger import info

        ticker_upper = ticker.upper()

        info(f"   🔍 ОПРЕДЕЛЕНИЕ ТИПА ЗАЯВКИ ДЛЯ {ticker_upper}:")

        # ========== 1. PRE-MARKET (только лимитные) ==========
        if is_pre_market_time():
            info(f"      🌅 Pre-market сессия → ИСПОЛЬЗУЕМ ЛИМИТНУЮ")
            return True, "pre-market session (limit orders only)"

        # ========== 2. OTC РЕЖИМ (только лимитные) ==========
        if is_otc_trading_time():
            info(f"      🌙 OTC режим → ИСПОЛЬЗУЕМ ЛИМИТНУЮ")
            return True, "OTC trading session (limit orders only)"

        # ========== 3. ПРОВЕРКА ЧЕРЕЗ API ==========
        status = self.get_order_types_status(ticker_upper)

        if not status.get('market_allowed', True):
            info(f"      ❌ Рыночные заявки НЕ ДОСТУПНЫ для {ticker_upper}")
            info(f"      📋 → ИСПОЛЬЗУЕМ ЛИМИТНУЮ ЗАЯВКУ")
            return True, f"market orders not allowed for {ticker_upper}"

        if not status.get('limit_allowed', True):
            info(f"      ⚠️ Лимитные заявки НЕ ДОСТУПНЫ для {ticker_upper}")
            info(f"      🟢 → ИСПОЛЬЗУЕМ РЫНОЧНУЮ ЗАЯВКУ")
            return False, f"limit orders not allowed for {ticker_upper}"

        # ========== 4. OTC ИНСТРУМЕНТЫ ==========
        if status.get('is_otc', False):
            info(f"      🔐 OTC инструмент → ИСПОЛЬЗУЕМ ЛИМИТНУЮ ЗАЯВКУ")
            return True, f"OTC instrument (limit orders only)"

        # ========== 5. ОСТАЛЬНЫЕ СЛУЧАИ - ПРЕДПОЧИТАЕМ РЫНОЧНЫЕ ==========
        info(f"      ✅ Рыночные заявки ДОСТУПНЫ → ИСПОЛЬЗУЕМ РЫНОЧНУЮ")
        return False, "market orders preferred"

    def is_market_orders_allowed(self, ticker: str) -> bool:
        """
        ПРОВЕРКА, РАЗРЕШЕНЫ ЛИ РЫНОЧНЫЕ ЗАЯВКИ ДЛЯ ИНСТРУМЕНТА
        БЕЗ ХАРДКОДА - ТОЛЬКО ЧЕРЕЗ API

        Args:
            ticker: Тикер инструмента

        Returns:
            True если рыночные заявки разрешены, False если только лимитные
        """
        status = self.get_order_types_status(ticker)
        return status.get('market_allowed', True)

    def is_otc_instrument(self, figi: str, ticker: str) -> Tuple[bool, str]:
        """
        ПРОВЕРКА, ЯВЛЯЕТСЯ ЛИ ИНСТРУМЕНТ OTC (ВНЕБИРЖЕВЫМ)
        OTC инструменты НЕЛЬЗЯ закрыть через API!

        🔍 ДЕТАЛЬНОЕ ЛОГИРОВАНИЕ КАЖДОГО ШАГА

        Args:
            figi: FIGI инструмента
            ticker: Тикер для логирования

        Returns:
            Tuple[bool, str]: (is_otc, причина)
        """
        from trading_bot.api.tbank_client import tbank
        from trading_bot.logger import info, warning, debug

        info(f"\n   🔍 ПРОВЕРКА OTC ДЛЯ {ticker}:")

        try:
            # ========== 1. ПРОВЕРКА ЧЕРЕЗ get_trading_status ==========
            debug(f"      📡 ШАГ 1/4: get_trading_status()")
            status = tbank.get_trading_status(figi)

            market_available = status.get('market_order_available', False)
            limit_available = status.get('limit_order_available', False)
            api_available = status.get('api_trade_available', False)

            debug(f"         API доступна: {'✅' if api_available else '❌'}")
            debug(f"         Рыночные заявки: {'✅' if market_available else '❌'}")
            debug(f"         Лимитные заявки: {'✅' if limit_available else '❌'}")

            # Если нет ни рыночных, ни лимитных заявок - OTC
            if not market_available and not limit_available:
                warning(f"      🔐 {ticker}: НЕТ доступных типов заявок → OTC")
                return True, "нет доступных типов заявок (OTC)"

            # ========== 2. ПРОВЕРКА ЧЕРЕЗ is_confirmation_required ==========
            debug(f"      📡 ШАГ 2/4: is_confirmation_required()")
            if tbank.is_confirmation_required(figi):
                warning(f"      🔐 {ticker}: ТРЕБУЕТ подтверждения сделок → OTC")
                return True, "требует подтверждения сделок (OTC)"

            # ========== 3. ПРОВЕРКА БИРЖИ ==========
            debug(f"      📡 ШАГ 3/4: получение информации об инструменте")
            instrument = tbank._get_instrument_by_figi(figi)
            if instrument:
                exchange = instrument.get('exchange', '')
                debug(f"         Биржа: {exchange}")

                if 'DEALER' in exchange or 'dealer' in exchange.lower():
                    warning(f"      🔐 {ticker}: внебиржевой инструмент (exchange={exchange}) → OTC")
                    return True, f"внебиржевой инструмент (exchange={exchange})"

            # ========== 4. ПРОВЕРКА СТАКАНА (дополнительно) ==========
            debug(f"      📡 ШАГ 4/4: проверка стакана")
            try:
                orderbook = tbank.get_orderbook(figi, depth=1)
                if orderbook:
                    bid_exists = orderbook.get('best_bid') is not None and orderbook.get('bid_volume', 0) > 0
                    ask_exists = orderbook.get('best_ask') is not None and orderbook.get('ask_volume', 0) > 0
                    debug(f"         Заявки на покупку: {'✅ есть' if bid_exists else '❌ нет'}")
                    debug(f"         Заявки на продажу: {'✅ есть' if ask_exists else '❌ нет'}")

                    # Если нет заявок ни с одной стороны — подозрение на OTC
                    if not bid_exists and not ask_exists:
                        warning(f"      ⚠️ {ticker}: пустой стакан (нет заявок) → подозрение на OTC")
                        # Не возвращаем OTC, но логируем
            except Exception as e:
                debug(f"         ⚠️ Ошибка проверки стакана: {e}")

            # ========== ИТОГ ==========
            info(f"      ✅ {ticker}: НЕ OTC (можно торговать)")
            return False, "OK"

        except Exception as e:
            warning(f"      ⚠️ Ошибка проверки OTC для {ticker}: {e}")
            return False, f"ошибка проверки: {e}"


# Глобальный экземпляр
instrument_filter = InstrumentFilter()