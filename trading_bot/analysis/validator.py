"""Валидация тикеров перед торговлей"""

from typing import Tuple, Dict, Any, Optional
from datetime import datetime, timedelta

from ..config import config
from ..logger import info, success, error, warning, debug

from ..utils.time_utils import is_trading_time_for_ticker, is_friday_evening
from ..core.blacklist_manager import blacklist_manager
from trading_bot.cache import TTLCache
from trading_bot.cache.cache_manager import TTLCache as UnifiedCache
USE_UNIFIED_CACHE = False


def _get_tbank():
    from trading_bot.api.tbank_client import tbank
    return tbank


class TickerValidator:
    """Валидатор тикеров - проверяет ликвидность, волатильность, объёмы"""

    def __init__(self, bot=None):
        self.bot = bot
        from trading_bot.cache import TTLCache
        self._validation_cache = TTLCache(default_ttl=300, max_size=1000, name="validator_cache")
        
        if USE_UNIFIED_CACHE:
            self._unified_cache = UnifiedCache(default_ttl=300, name="validator")
        self._cache_ttl = 3600  # 1 час
        self._initialized = False
        info("🔧 TickerValidator инициализирован")

    def initialize(self, bot):
        """Инициализация с ботом (для отложенной инициализации)"""
        self.bot = bot
        self._initialized = True
        info("✅ TickerValidator привязан к боту")

    def get_cached_validation(self, ticker: str) -> Optional[Dict]:
        """Получение кэшированной валидации"""
        try:
            cache_entry = self._validation_cache.get(ticker)
            if cache_entry is not None:
                # Проверяем время (хотя TTLCache уже управляет TTL)
                if datetime.now() - cache_entry.get('time', datetime.min) < timedelta(seconds=self._cache_ttl):
                    return cache_entry.get('data')
        except Exception as e:
            debug(f"Ошибка получения кэша для {ticker}: {e}")
        return None

    def _check_liquidity(self, figi: str, ticker: str) -> Dict[str, Any]:
        """
        Проверка ликвидности инструмента через стакан.

        Args:
            figi (str): FIGI инструмента
            ticker (str): Тикер для логирования

        Returns:
            Dict[str, Any]: Результат проверки ликвидности
        """
        from trading_bot.api.tbank_client import tbank

        try:
            orderbook = tbank.get_orderbook(figi, depth=3)

            if not orderbook:
                return {
                    'is_liquid': False,
                    'reason': 'Не удалось получить стакан заявок',
                    'bid_volume_rub': 0,
                    'ask_volume_rub': 0,
                    'spread_pct': None
                }

            best_bid = orderbook.get('best_bid')
            best_ask = orderbook.get('best_ask')
            bid_volume_rub = orderbook.get('bid_volume', 0) * (best_bid or 0)
            ask_volume_rub = orderbook.get('ask_volume', 0) * (best_ask or 0)

            spread_pct = None
            if best_bid and best_ask and best_bid > 0:
                spread_pct = (best_ask - best_bid) / best_bid * 100

            MIN_VOLUME_RUB = 5000
            MAX_SPREAD_PCT = 0.5

            is_bid_liquid = bid_volume_rub >= MIN_VOLUME_RUB
            is_ask_liquid = ask_volume_rub >= MIN_VOLUME_RUB
            is_spread_ok = spread_pct is None or spread_pct <= MAX_SPREAD_PCT

            is_liquid = is_bid_liquid and is_ask_liquid and is_spread_ok

            reason = None
            if not is_liquid:
                if not is_bid_liquid:
                    reason = f"Малый объём покупки: {bid_volume_rub:.0f}₽ (нужно {MIN_VOLUME_RUB}₽)"
                elif not is_ask_liquid:
                    reason = f"Малый объём продажи: {ask_volume_rub:.0f}₽ (нужно {MIN_VOLUME_RUB}₽)"
                elif not is_spread_ok:
                    reason = f"Слишком большой спред: {spread_pct:.2f}% (макс {MAX_SPREAD_PCT}%)"

            return {
                'is_liquid': is_liquid,
                'reason': reason,
                'bid_volume_rub': bid_volume_rub,
                'ask_volume_rub': ask_volume_rub,
                'best_bid': best_bid,
                'best_ask': best_ask,
                'spread_pct': spread_pct
            }

        except Exception as e:
            debug(f"Ошибка проверки ликвидности для {ticker}: {e}")
            return {
                'is_liquid': False,
                'reason': f'Ошибка проверки: {e}',
                'bid_volume_rub': 0,
                'ask_volume_rub': 0,
                'spread_pct': None
            }

    def validate(self, ticker: str, force: bool = False) -> Tuple[bool, Dict[str, Any]]:
        """
        Валидация тикера перед торговлей.

        Args:
            ticker (str): Тикер для проверки
            force (bool): Принудительная проверка (игнорировать кэш)

        Returns:
            Tuple[bool, Dict[str, Any]]: (валиден_ли, детали_проверки)
        """
        from trading_bot.api.tbank_client import tbank
        from trading_bot.logger import warning

        # Проверка кэша (если не force)
        if not force:
            cached = self.get_cached_validation(ticker)
            if cached is not None:
                debug(f"📦 Используем кэш для {ticker}")
                return cached.get('is_valid', False), cached.get('details', {})

        details = {
            'ticker': ticker,
            'is_valid': False,
            'reasons': []
        }

        # 1. Проверка FIGI
        figi = tbank._get_figi_by_ticker(ticker) if self.bot else None
        if not figi:
            details['reasons'].append(f"FIGI не найден для {ticker}")
            return False, details

        details['figi'] = figi

        # 2. Проверка чёрного списка
        if self.bot and self.bot._is_blacklisted(ticker):
            details['reasons'].append(f"{ticker} в чёрном списке")
            return False, details

        # ========== ПРОВЕРКА OTC ==========
        try:
            if tbank.is_confirmation_required(figi):
                warning(f"⛔ {ticker} - OTC инструмент, торговля через API невозможна")
                details['reasons'].append(f"{ticker} требует подтверждения сделок (OTC)")
                if self.bot:
                    self.bot._add_to_blacklist(ticker, minutes=60)
                # Сохраняем в кэш
                self._validation_cache.set(ticker, {
                    'time': datetime.now(),
                    'is_valid': False,
                    'details': details
                }, ttl=self._cache_ttl)
                return False, details
        except Exception as e:
            warning(f"⚠️ Ошибка проверки OTC для {ticker}: {e}")
            details['reasons'].append(f"Ошибка проверки OTC: {e}")
            return False, details
        # ========== КОНЕЦ ПРОВЕРКИ OTC ==========

        # 4. Проверка доступности торгов
        is_available, reason = tbank.is_market_available(figi)
        if not is_available:
            details['reasons'].append(f"Рынок недоступен: {reason}")
            return False, details

        # 5. Проверка ликвидности
        liquidity_check = self._check_liquidity(figi, ticker)
        if not liquidity_check.get('is_liquid', False):
            details['reasons'].append(f"Недостаточная ликвидность: {liquidity_check.get('reason', '')}")
            return False, details

        details['is_valid'] = True
        details['reasons'].append("✅ Все проверки пройдены")

        # Сохраняем в кэш
        self._validation_cache.set(ticker, {
            'time': datetime.now(),
            'is_valid': True,
            'details': details
        }, ttl=self._cache_ttl)

        return True, details

    def validate_full(self, ticker: str, force: bool = False) -> Tuple[bool, Dict[str, Any]]:
        """
        ПОЛНАЯ ВАЛИДАЦИЯ ТИКЕРА ПЕРЕД ВХОДОМ
        Включает проверку чёрного списка и времени торгов
        """
        ticker = ticker.upper()

        info(f"\n{'═' * 50}")
        info(f"🔍 ПОЛНАЯ ВАЛИДАЦИЯ ТИКЕРА: {ticker}")
        info(f"{'═' * 50}")

        # ========== 1. ПРОВЕРКА ЧЁРНОГО СПИСКА ==========
        info(f"📋 ШАГ 1/5: Проверка чёрного списка...")
        is_blocked, reason = blacklist_manager.is_blocked(ticker)
        if is_blocked:
            error(f"❌ {ticker} ЗАБЛОКИРОВАН: {reason}")
            return False, {'error': reason, 'blocked': True}
        info(f"   ✅ Чёрный список: чисто")

        # ========== 2. ПРОВЕРКА ВРЕМЕНИ ТОРГОВ ==========
        info(f"📋 ШАГ 2/5: Проверка времени торгов...")
        can_trade, trade_reason = is_trading_time_for_ticker(ticker)
        if not can_trade:
            error(f"❌ {ticker} НЕЛЬЗЯ ТОРГОВАТЬ: {trade_reason}")
            blacklist_manager.report_error(ticker, "TIME_RESTRICTION")
            return False, {'error': trade_reason, 'time_restricted': True}
        info(f"   ✅ Время торгов: OK ({trade_reason})")

        # ========== 3. ВЫПОЛНЯЕМ ОСНОВНУЮ ВАЛИДАЦИЮ ==========
        info(f"📋 ШАГ 3/5: Основная валидация...")
        valid, data = self.validate(ticker, force)

        if not valid:
            error(f"❌ Основная валидация НЕ ПРОЙДЕНА: {data.get('error', 'неизвестная причина')}")
            blacklist_manager.report_error(ticker, data.get('error', 'VALIDATION_FAILED'))
            return False, data

        # ========== 4. ПРОВЕРКА ЛИКВИДНОСТИ (уже в validate) ==========
        info(f"📋 ШАГ 4/5: Проверка ликвидности...")
        # (уже выполнено в validate)

        # ========== 5. ДОПОЛНИТЕЛЬНЫЕ ПРОВЕРКИ ==========
        info(f"📋 ШАГ 5/5: Дополнительные проверки...")

        # ❌ УДАЛЕНА ПРОВЕРКА НА BLUE CHIPS
        # if is_friday_evening():
        #     blue_chips = [...]  # ЭТО УДАЛЕНО

        success(f"✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ для {ticker}!")
        info(f"{'═' * 50}\n")

        return True, data

    def validate_liquidity(self, ticker: str, required_rub_volume: int = 5000) -> Tuple[bool, str]:
        """
        Проверить ликвидность тикера через стакан

        Args:
            ticker: Тикер акции
            required_rub_volume: Минимальный объём в рублях

        Returns:
            (is_valid, reason)
        """
        from trading_bot.api.tbank_client import tbank

        info(f"🔍 ПРОВЕРКА ЛИКВИДНОСТИ для {ticker} (мин.объём: {required_rub_volume}₽)")

        # Получаем FIGI по тикеру
        figi = tbank._get_figi_by_ticker(ticker) if hasattr(self, 'bot') else None
        if not figi:
            warning(f"❌ {ticker}: FIGI не найден")
            return False, f"Тикер {ticker} не найден"

        info(f"   ✅ FIGI найден: {figi[:12]}...")

        # Проверяем ликвидность
        liquidity = tbank.check_liquidity(figi, required_rub_volume)

        # Детальное логирование
        info(f"   📊 РЕЗУЛЬТАТЫ ПРОВЕРКИ:")
        info(f"      🟢 BID: {liquidity.get('best_bid', 0):.2f}₽ (объём: {liquidity.get('bid_volume_rub', 0):.0f}₽)")
        info(f"      🔴 ASK: {liquidity.get('best_ask', 0):.2f}₽ (объём: {liquidity.get('ask_volume_rub', 0):.0f}₽)")
        info(f"      📊 Спред: {liquidity.get('spread_pct', 0):.2f}%")

        if not liquidity.get('is_liquid', False):
            reason = liquidity.get('reason', 'Недостаточная ликвидность')
            warning(f"❌ {ticker} НЕ ПРОШЁЛ проверку: {reason}")
            return False, reason

        success(f"✅ {ticker} ПРОШЁЛ проверку ликвидности!")
        return True, f"✅ Ликвидность OK (BID: {liquidity['bid_volume_rub']:.0f}₽, ASK: {liquidity['ask_volume_rub']:.0f}₽)"

    #     def _get_figi_by_ticker(self, ticker: str) -> Optional[str]:
    #         """Получить FIGI по тикеру"""
    #         from trading_bot.api.tbank_client import tbank

    #         info(f"🔍 Поиск FIGI для тикера {ticker}...")

    #         shares = tbank.get_all_shares()
    #         for share in shares:
    #             if share.get('ticker') == ticker.upper():
    #                 figi = share.get('figi')
    #                 info(f"   ✅ Найден FIGI: {figi[:12]}... для {ticker}")
    #                 return figi

    #         warning(f"   ❌ FIGI для {ticker} не найден")
    #         return None

    def _perform_validation(self, ticker: str) -> Tuple[bool, Dict[str, Any]]:
        """Выполнение валидации с проверкой ликвидности"""
        from trading_bot.api.tbank_client import tbank  # ← ПОДНЯТЬ В НАЧАЛО

        try:
            info(f"\n{'═' * 50}")
            info(f"🔍 ВАЛИДАЦИЯ ТИКЕРА: {ticker}")
            info(f"{'═' * 50}")

            # Если бот не инициализирован, пропускаем сложную валидацию
            if not self._initialized or self.bot is None:
                warning(f"⚠️ TickerValidator не инициализирован, базовая валидация для {ticker}")
                return True, {'warning': f'Базовая валидация для {ticker}', 'figi': None}

            # Шаг 1: Получаем FIGI
            info(f"📋 ШАГ 1/3: Получение FIGI...")
            figi = tbank._get_figi_by_ticker(ticker)
            if not figi:
                error(f"❌ Валидация НЕ ПРОЙДЕНА: FIGI не найден для {ticker}")
                return False, {'error': f'FIGI не найден для {ticker}'}

            info(f"   ✅ FIGI получен: {figi[:12]}...")

            # Шаг 2: Проверяем торговый статус
            info(f"📋 ШАГ 2/3: Проверка торгового статуса...")
            status = tbank.get_trading_status(figi)  # ← ИСПРАВЛЕНО:直接用 tbank
            if status:
                api_available = status.get('api_trade_available', False)
                market_available = status.get('market_order_available', False)
                limit_available = status.get('limit_order_available', False)

                info(f"   📊 Статус торгов:")
                info(f"      🔌 API торговля: {'✅' if api_available else '❌'}")
                info(f"      🏷️ Рыночные заявки: {'✅' if market_available else '❌'}")
                info(f"      📋 Лимитные заявки: {'✅' if limit_available else '❌'}")

                if not api_available:
                    error(f"❌ Валидация НЕ ПРОЙДЕНА: API торговля недоступна для {ticker}")
                    return False, {'error': 'API торговля недоступна', 'figi': figi}

            # Шаг 3: Проверка ликвидности через стакан
            info(f"📋 ШАГ 3/3: Проверка ликвидности через стакан...")
            liquidity = tbank.check_liquidity(figi, required_volume=5000)  # ← ИСПРАВЛЕНО

            # Детальный вывод результатов ликвидности
            info(f"   📊 РЕЗУЛЬТАТЫ ЛИКВИДНОСТИ:")
            info(f"      🟢 Лучший BID: {liquidity.get('best_bid', 0):.2f}₽")
            info(f"      🔴 Лучший ASK: {liquidity.get('best_ask', 0):.2f}₽")
            info(f"      📊 Объём BID: {liquidity.get('bid_volume_rub', 0):.0f}₽")
            info(f"      📊 Объём ASK: {liquidity.get('ask_volume_rub', 0):.0f}₽")
            info(f"      📈 Спред: {liquidity.get('spread_pct', 0):.2f}%")
            info(f"      💧 Статус: {'✅ ДОСТАТОЧНА' if liquidity.get('is_liquid') else '❌ НЕДОСТАТОЧНА'}")

            if not liquidity.get('is_liquid', False):
                reason = liquidity.get('reason', 'Недостаточная ликвидность')
                error(f"❌ Валидация НЕ ПРОЙДЕНА: {reason}")
                return False, {
                    'error': reason,
                    'figi': figi,
                    'liquidity_check': liquidity
                }

            success(f"✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ для {ticker}!")
            info(f"{'═' * 50}\n")

            return True, {
                'figi': figi,
                'warning': 'Базовая валидация',
                'liquidity': liquidity
            }

        except Exception as e:
            error(f"❌ Ошибка валидации {ticker}: {e}")
            debug(f"   Детали: {e}")
            return False, {'error': str(e)}

    def clear_cache(self):
        """Очистка кэша валидации"""
        self._validation_cache.clear()
        info("🧹 Кэш валидации очищен")

    def add_to_blacklist(self, ticker: str, permanent: bool = False):
        """
        Добавить тикер в чёрный список

        Args:
            ticker: Тикер для блокировки
            permanent: Если True - блокировка навсегда, иначе на 24 часа
        """
        if permanent:
            blacklist_manager.add_permanent(ticker)
        else:
            blacklist_manager.add_temporary(ticker)

    def remove_from_blacklist(self, ticker: str):
        """Удалить тикер из временного чёрного списка"""
        blacklist_manager.clear_temporary(ticker)

    def get_blacklist_status(self) -> Dict:
        """Получить статус чёрного списка"""
        return blacklist_manager.get_status()

    def report_error(self, ticker: str, error_code: str = ""):
        """
        Сообщить об ошибке для тикера (для авто-блокировки)

        Args:
            ticker: Тикер
            error_code: Код ошибки (опционально)
        """
        blacklist_manager.report_error(ticker, error_code)


# ✅ ИСПРАВЛЕНО: Глобальный экземпляр создаётся, но без бота
validator = TickerValidator()  # Бот будет добавлен позже через initialize()
