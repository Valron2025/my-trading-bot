# trading_bot/analysis/instrument_filter.py
"""Фильтрация низколиквидных и OTC инструментов"""

import os
import asyncio
from typing import Dict, Any, Tuple, List
from datetime import datetime, timedelta, timezone

from trading_bot.logger import info, warning, debug, error
from trading_bot.config import config

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
        self.min_share_price = float(os.getenv('MIN_SHARE_PRICE', '1'))
        self.max_share_price = float(os.getenv('MAX_SHARE_PRICE', '2000'))

        # ========== OTC ПОРОГИ (для выходных и офф-часов) ==========
        self.otc_min_avg_volume = int(os.getenv('OTC_MIN_AVG_VOLUME', '5000'))
        self.otc_min_volume_ratio = float(os.getenv('OTC_MIN_VOLUME_RATIO', '0.3'))
        self.otc_min_trade_amount = int(os.getenv('OTC_MIN_TRADE_AMOUNT', '200'))

        # ========== БИРЖЕВЫЕ ПОРОГИ (для обычных торгов) ==========
        self.exchange_min_avg_volume = self.min_avg_volume
        self.exchange_min_volume_ratio = self.min_volume_ratio
        self.exchange_min_trade_amount = config.min_trade_amount

        # Чёрный и белый списки
        self.blacklist_tickers = self._load_blacklist()
        self.whitelist_tickers = {'SBER', 'GAZP', 'LKOH', 'ROSN', 'TATN', 'NVTK', 'MGNT'}

        info(f"🔧 InstrumentFilter инициализирован")
        info(f"   📊 Биржевой режим: мин.объём={self.exchange_min_avg_volume:,}, мин.лота={self.exchange_min_trade_amount}₽")
        info(f"   🌙 OTC режим: мин.объём={self.otc_min_avg_volume:,}, мин.лота={self.otc_min_trade_amount}₽")
        info(f"   ⚡ Мин. соотношение объёма: {self.min_volume_ratio}x")
        info(f"   📈 Макс. спред: {self.max_spread_pct}%")
        info(f"   ⛔ Чёрный список: {len(self.blacklist_tickers)} тикеров")

    def update_for_otc_mode(self, is_otc: bool):
        """Обновление параметров в зависимости от режима торгов"""
        if is_otc:
            self.min_avg_volume = self.otc_min_avg_volume
            self.min_volume_ratio = self.otc_min_volume_ratio
            # Обновляем глобальный конфиг для минимальной суммы сделки
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
            return set(ticker.strip().upper() for ticker in blacklist_str.split(','))

        # Дефолтный чёрный список
        return {
            'RZSB', 'KCHE', 'KCHEP', 'LPSB', 'MSRS', 'NNSB', 'OMSK',
            'PRMB', 'ROSB', 'RTKM', 'SAGO', 'SELG', 'TASB', 'TGKA',
            'TGKB', 'TGKD', 'TGKZ', 'UCSS', 'URKA', 'VJGZ', 'VLHZ'
        }

    def is_otc(self, figi: str) -> bool:
        """Проверка OTC инструмента"""
        # Проверяем кэш
        if figi in self.otc_cache and figi in self.otc_cache_time:
            if datetime.now(MOSCOW_TZ) - self.otc_cache_time[figi] < timedelta(seconds=self.cache_ttl):
                return self.otc_cache[figi]

        try:
            tbank = _get_tbank()
            all_shares = tbank.get_all_shares(limit=1000)

            for stock in all_shares:
                if stock.get('figi') == figi:
                    # OTC определение
                    exchange = stock.get('exchange', '')
                    is_qual = stock.get('for_qual_investor_flag', False)

                    is_otc = (exchange == 'INSTRUMENT_EXCHANGE_DEALER') or is_qual

                    self.otc_cache[figi] = is_otc
                    self.otc_cache_time[figi] = datetime.now(MOSCOW_TZ)

                    if is_otc:
                        debug(f"🌙 OTC инструмент: {stock.get('ticker', figi[:8])}")

                    return is_otc

            return False

        except Exception as e:
            warning(f"Ошибка проверки OTC для {figi}: {e}")
            return False

    def is_liquid(self, ticker: str, price: float) -> bool:
        """Проверка ликвидности инструмента (для совместимости со Scanner)"""
        # Для демо-режима возвращаем True для всех тикеров
        if ticker in self.whitelist_tickers:
            return True

        # Базовая проверка по цене
        if price < self.min_share_price or price > self.max_share_price:
            return False

        # Если тикер в чёрном списке
        if ticker in self.blacklist_tickers:
            return False

        return True

    def check_liquidity(self, ticker: str, candles: List[Tuple[float, float]]) -> Tuple[bool, str]:
        """
        Проверка ликвидности инструмента

        Returns:
            (is_liquid, reason)
        """
        if not candles or len(candles) < 10:
            return False, f"недостаточно данных ({len(candles)} свечей)"

        # Получаем объёмы
        volumes = [int(c[1]) for c in candles if c[1] > 0]
        if not volumes:
            return False, "нет данных об объёмах"

        # Средний объём
        avg_volume = sum(volumes) / len(volumes)

        if avg_volume < self.min_avg_volume:
            return False, f"средний объём {avg_volume:,.0f} < {self.min_avg_volume:,}"

        # Проверка последнего объёма относительно среднего
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

            # Пытаемся получить реальный спред через orderbook
            try:
                # Пробуем получить стакан
                instrument_id = int(figi) if figi.isdigit() else figi
                orderbook = tbank.get_orderbook(instrument_id)

                if orderbook and 'bids' in orderbook and 'asks' in orderbook:
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
        """Проверка спреда по тикеру (через поиск FIGI)"""
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
        """
        Универсальная проверка спреда (по FIGI или тикеру)

        Args:
            identifier: Может быть FIGI (начинается с BBG или длинная цифровая строка) или тикер

        Returns:
            (is_ok, spread_pct, reason)
        """
        # Определяем, что передано: FIGI или тикер
        is_figi = identifier.startswith('BBG') or (len(identifier) > 10 and identifier.isdigit())

        if is_figi:
            return self.check_spread_by_figi(identifier)
        else:
            return self.check_spread_by_ticker(identifier)

    def check_trading_quality(self, ticker: str) -> Tuple[bool, str]:
        """Проверка качества торговли по тикеру"""
        ticker = ticker.upper()

        # Белый список
        if ticker in self.whitelist_tickers:
            return True, "белый список"

        # Чёрный список
        if ticker in self.blacklist_tickers:
            return False, "чёрный список"

        # Проверка на OTC
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

    def filter_candidates(self, candidates: List, is_otc_mode: bool = None) -> List:
        """
        Фильтрация списка кандидатов без хардкода
        """
        from trading_bot.config import config
        from trading_bot.logger import info, debug, warning

        if not candidates:
            return []

        from trading_bot.api.tbank_client import tbank

        # ✅ ПРОВЕРКА ДЛЯ МАЛОГО КАПИТАЛА - ПРОПУСКАЕМ ПРОВЕРКУ ЛИКВИДНОСТИ
        if config.total_capital < 5000:
            info(f"   💰 Малый капитал ({config.total_capital:.0f}₽) — пропускаем проверку ликвидности")
            filtered = [c for c in candidates if 1 <= c.price <= 2000]
            if filtered:
                return filtered
            return candidates[:5]

        # Определяем режим фильтрации
        if is_otc_mode is None:
            is_otc_mode = getattr(config, 'is_otc_mode', False)

        # Сохраняем старые пороги для восстановления
        old_min_volume = self.min_avg_volume
        old_min_ratio = self.min_volume_ratio
        old_min_trade = config.min_trade_amount

        # Статистика фильтрации
        stats = {
            'total': len(candidates),
            'confirmation_required': 0,
            'otc': 0,
            'low_liquidity': 0,
            'wide_spread': 0,
            'price_out_of_range': 0,
            'passed': 0
        }

        filtered = []

        try:
            # Применяем пороги в зависимости от режима
            if is_otc_mode:
                self.min_avg_volume = self.otc_min_avg_volume
                self.min_volume_ratio = self.otc_min_volume_ratio
                config.min_trade_amount = self.otc_min_trade_amount
                debug(
                    f"🌙 Фильтрация в OTC режиме: мин.объём={self.min_avg_volume}, мин.лота={config.min_trade_amount}₽")
            else:
                self.min_avg_volume = self.exchange_min_avg_volume
                self.min_volume_ratio = self.exchange_min_volume_ratio
                config.min_trade_amount = self.exchange_min_trade_amount
                debug(
                    f"🏛️ Фильтрация в биржевом режиме: мин.объём={self.min_avg_volume}, мин.лота={config.min_trade_amount}₽")

            # Основной цикл фильтрации
            for cand in candidates:
                ticker = cand.ticker
                figi = cand.figi

                # 1. ПРОВЕРКА: требует ли инструмент подтверждения сделок (ошибка 30240)
                if tbank.is_confirmation_required(figi):
                    stats['confirmation_required'] += 1
                    debug(f"   ⏭️ {ticker}: требует подтверждения сделок (30240) — пропускаем")
                    continue

                # 2. ПРОВЕРКА OTC по FIGI
                if hasattr(self, 'is_otc') and self.is_otc(figi):
                    stats['otc'] += 1
                    debug(f"   ⏭️ {ticker}: OTC инструмент (FIGI)")
                    continue

                # 3. ПРОВЕРКА цены
                if cand.price <= 0 or cand.price > 50000:
                    stats['price_out_of_range'] += 1
                    debug(f"   ⏭️ {ticker}: цена вне диапазона ({cand.price:.2f}₽)")
                    continue

                # 4. ПРОВЕРКА ликвидности (через свечи)
                try:
                    from trading_bot.core.candle_sync_wrapper import get_candles_sync
                    candles = get_candles_sync(ticker, days=5)
                    if candles and len(candles) >= 10:
                        volumes = [int(c[1]) for c in candles if c[1] > 0]
                        if volumes:
                            avg_volume = sum(volumes) / len(volumes)
                            if avg_volume < self.min_avg_volume:
                                stats['low_liquidity'] += 1
                                debug(
                                    f"   ⏭️ {ticker}: низкая ликвидность ({avg_volume:,.0f} < {self.min_avg_volume:,})")
                                continue
                        else:
                            stats['low_liquidity'] += 1
                            debug(f"   ⏭️ {ticker}: нет данных об объёмах")
                            continue
                    else:
                        # Для малого капитала даём шанс
                        if config.total_capital > 5000:
                            stats['low_liquidity'] += 1
                            debug(f"   ⏭️ {ticker}: недостаточно данных ({len(candles) if candles else 0} свечей)")
                            continue
                except Exception as e:
                    debug(f"   ⏭️ {ticker}: ошибка проверки ликвидности - {e}")
                    continue

                # 5. ПРОВЕРКА спреда (опционально)
                if hasattr(self, 'check_spread') and callable(self.check_spread):
                    try:
                        spread_ok, spread_pct, spread_reason = self.check_spread(figi)
                        if not spread_ok:
                            stats['wide_spread'] += 1
                            debug(f"   ⏭️ {ticker}: широкий спред ({spread_pct:.2f}%) - {spread_reason}")
                            continue
                    except Exception as e:
                        debug(f"   ⏭️ {ticker}: ошибка проверки спреда - {e}")
                        continue

                # Все проверки пройдены
                stats['passed'] += 1
                filtered.append(cand)

        except Exception as e:
            error(f"❌ Ошибка в filter_candidates: {e}")
            import traceback
            error(traceback.format_exc())
        finally:
            # Восстанавливаем пороги
            self.min_avg_volume = old_min_volume
            self.min_volume_ratio = old_min_ratio
            config.min_trade_amount = old_min_trade

        # Логируем статистику
        if stats['passed'] < stats['total']:
            info(f"\n📊 ФИЛЬТРАЦИЯ ИНСТРУМЕНТОВ:")
            info(f"   📥 Всего: {stats['total']}")
            if stats['confirmation_required'] > 0:
                info(f"   🔐 Требуют подтверждения: {stats['confirmation_required']}")
            if stats['otc'] > 0:
                info(f"   🌙 OTC: {stats['otc']}")
            if stats['low_liquidity'] > 0:
                info(f"   💧 Низкая ликвидность: {stats['low_liquidity']}")
            if stats['wide_spread'] > 0:
                info(f"   📏 Широкий спред: {stats['wide_spread']}")
            if stats['price_out_of_range'] > 0:
                info(f"   💰 Цена вне диапазона: {stats['price_out_of_range']}")
            if stats['passed'] > 0:
                info(f"   ✅ Прошли: {stats['passed']}")
        else:
            info(f"✅ Все {stats['passed']} инструментов прошли фильтрацию")

        return filtered

    def check_liquidity_with_thresholds(self, ticker: str, candles: List[Tuple[float, float]],
                                        min_volume: int, min_ratio: float) -> Tuple[bool, str]:
        """
        Проверка ликвидности с заданными порогами
        """
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
        """Получение причины блокировки инструмента"""
        ticker = ticker.upper()

        if ticker in self.whitelist_tickers:
            return ""

        if ticker in self.blacklist_tickers:
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
        """Получение статистики фильтра"""
        return {
            'min_avg_volume': self.min_avg_volume,
            'min_volume_ratio': self.min_volume_ratio,
            'max_spread_pct': self.max_spread_pct,
            'blacklist_size': len(self.blacklist_tickers),
            'whitelist_size': len(self.whitelist_tickers),
            'otc_cache_size': len(self.otc_cache)
        }


# Глобальный экземпляр
instrument_filter = InstrumentFilter()