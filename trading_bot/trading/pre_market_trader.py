#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pre_market_trader.py - ТОРГОВЛЯ В PRE-MARKET И ВЕЧЕРНЮЮ СЕССИЮ
ПОЛНАЯ ИНТЕГРАЦИЯ С ВСЕЙ АНАЛИТИКОЙ ПРОЕКТА

Особенности:
- Использует TechnicalAnalyzer (все индикаторы)
- Использует FundamentalAnalyzer
- Поддерживает динамические стоп-лоссы и тейк-профиты
- Автоматический выбор тикеров
- Адаптивные настройки под капитал
"""

import os
import asyncio
import threading
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

from ..config import config
from ..logger import info, success, error, warning, debug

MOSCOW_TZ = timezone(timedelta(hours=3))


class TradingSession(Enum):
    """Торговые сессии"""
    PRE_MARKET = "pre_market"  # 06:50-09:50 (утренняя сессия, ИСПРАВЛЕНО!)
    MAIN = "main"  # 09:50-18:59
    EVENING = "evening"  # 19:00-23:49
    WEEKEND = "weekend"  # Выходные (09:50-18:59)
    CLOSED = "closed"  # Закрыто


@dataclass
class PreMarketOrder:
    """Pre-market ордер с полной информацией"""
    order_id: str = ""
    ticker: str = ""
    figi: str = ""
    direction: str = ""  # BUY или SELL
    quantity: int = 0
    limit_price: float = 0.0
    confidence: float = 0.0
    score: int = 0  # Исходный score
    final_score: int = 0  # С учётом фундаментального анализа
    reason: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(MOSCOW_TZ))
    updated_at: Optional[datetime] = None
    expires_at: datetime = field(default_factory=lambda: datetime.now(MOSCOW_TZ).replace(hour=23, minute=59, second=59))
    status: str = "PENDING"  # PENDING, ACTIVE, FILLED, EXPIRED, CANCELLED, REJECTED
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    entry_price: float = 0.0  # Фактическая цена исполнения
    analysis_data: Dict[str, Any] = field(default_factory=dict)  # Сохраняем анализ


class PreMarketTrader:
    """
    ТОРГОВЛЯ В PRE-MARKET И ВЕЧЕРНЮЮ СЕССИЮ
    ПОЛНАЯ ИНТЕГРАЦИЯ С ВСЕЙ АНАЛИТИКОЙ ПРОЕКТА

    Возможности:
    - Использует TechnicalAnalyzer (все индикаторы: RSI, MACD, Bollinger, свечные паттерны, уровни)
    - Использует FundamentalAnalyzer (P/E, ROE, дивиденды)
    - Автоматический выбор тикеров из CSV
    - Адаптивные настройки под капитал
    - Динамические стоп-лоссы и тейк-профиты
    - Мониторинг исполнения ордеров
    """

    def __init__(self, bot):
        self.bot = bot
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._monitor_task: Optional[asyncio.Task] = None

        # Хранилища ордеров
        self.pending_orders: List[PreMarketOrder] = []
        self.active_orders: List[PreMarketOrder] = []
        self.filled_orders: List[PreMarketOrder] = []
        self.rejected_orders: List[PreMarketOrder] = []

        # ========== НАСТРОЙКИ (АВТОМАТИЧЕСКИ) ==========
        self.max_orders_per_day = 5  # Максимум ордеров в день
        # ✅ ИСПРАВЛЕНО: начальное значение (будет перезаписано в _update_dynamic_params)
        self.max_capital_per_order = 20000  # Стартовое значение, будет пересчитано
        self.min_score_threshold = 3  # Минимальный score для входа

        # ========== ОТКЛЮЧАЕМ ФУНДАМЕНТАЛЬНЫЙ АНАЛИЗ ==========
        self.use_fundamental = False
        self.fundamental_enabled = False
        self.pre_market_start_hour = 6
        self.pre_market_start_minute = 50

        # Динамические параметры (будут рассчитаны под капитал)
        self.dynamic_stop_loss_pct = 0.5
        self.dynamic_take_profit_pct = 1.0
        self.dynamic_trailing_stop_pct = 0.3

        # Список тикеров
        self._pre_market_tickers: List[str] = []
        self._tickers_loaded = False

        # Кэш для анализа
        self._analysis_cache: Dict[str, Any] = {}
        self._cache_ttl = timedelta(minutes=30)

        info(f"🌅 PreMarketTrader инициализирован (ПОЛНАЯ ВЕРСИЯ)")
        info(f"   Макс. ордеров в день: {self.max_orders_per_day}")
        info(f"   Мин. score для входа: {self.min_score_threshold}")
        info(f"   Фундаментальный анализ (pre-market): {'✅ ВКЛ' if self.fundamental_enabled else '❌ ВЫКЛ'}")
        info(f"   Pre-market начало: {self.pre_market_start_hour:02d}:{self.pre_market_start_minute:02d}")

    # ========== ОПРЕДЕЛЕНИЕ СЕССИИ ==========

    def get_current_session(self) -> TradingSession:
        """Определение текущей торговой сессии"""
        now = datetime.now(MOSCOW_TZ)
        current_time = now.time()
        weekday = now.weekday()

        # Выходные (суббота, воскресенье)
        if weekday >= 5:
            # Выходные: 10:00 - 18:59 (по документации)
            weekend_start = dt_time(10, 0)
            weekend_end = dt_time(18, 59)
            if weekend_start <= current_time <= weekend_end:
                return TradingSession.WEEKEND
            return TradingSession.CLOSED

        # Будни
        pre_market_start = dt_time(self.pre_market_start_hour, self.pre_market_start_minute)
        pre_market_end = dt_time(9, 50)
        main_start = dt_time(9, 50)
        main_end = dt_time(18, 59)
        evening_start = dt_time(19, 0)
        evening_end = dt_time(23, 49, 59)

        # Pre-market (утренняя сессия)
        if pre_market_start <= current_time < pre_market_end:
            return TradingSession.PRE_MARKET

        # Основная сессия
        if main_start <= current_time <= main_end:
            return TradingSession.MAIN

        # Вечерняя сессия
        if evening_start <= current_time <= evening_end:
            return TradingSession.EVENING

        return TradingSession.CLOSED

    def is_pre_market_time(self) -> bool:
        """Проверка, сейчас pre-market"""
        return self.get_current_session() == TradingSession.PRE_MARKET

    def is_main_session(self) -> bool:
        """Проверка, сейчас основная сессия"""
        return self.get_current_session() == TradingSession.MAIN

    def is_evening_session(self) -> bool:
        """Проверка, сейчас вечерняя сессия"""
        return self.get_current_session() == TradingSession.EVENING

    def can_trade_now(self) -> bool:
        """Можно ли торговать сейчас"""
        session = self.get_current_session()
        return session in [TradingSession.PRE_MARKET, TradingSession.MAIN,
                           TradingSession.EVENING, TradingSession.WEEKEND]

    # ========== ЗАГРУЗКА ТИКЕРОВ ==========

    def load_pre_market_tickers(self) -> List[str]:
        """Загрузка тикеров для pre-market из CSV"""
        import os
        print(f"🔍 Поиск CSV файлов в: {os.getcwd()}/instruments/")
        print(f"🔍 Текущая директория: {os.getcwd()}")
        print(f"📁 Папка instruments существует: {os.path.exists('instruments')}")
        print(f"📁 Файлы в instruments: {os.listdir('instruments') if os.path.exists('instruments') else 'НЕТ'}")
        if self._tickers_loaded:
            return self._pre_market_tickers

        tickers = set()

        # Поиск CSV файлов
        search_paths = [
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'instruments'),
            os.path.join(os.getcwd(), 'instruments'),
            os.path.join(os.getcwd(), 'data'),
        ]

        for search_path in search_paths:
            if not os.path.exists(search_path):
                continue

            for file in os.listdir(search_path):
                if file.endswith(('.csv', '.CSV', '.txt')):
                    file_path = os.path.join(search_path, file)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            for line in f:
                                line = line.strip()
                                if not line or line.startswith('#') or line.startswith('symbol'):
                                    continue
                                # Разделяем по запятой или пробелу
                                parts = line.split(',')
                                ticker = parts[0].strip().upper()
                                # Валидация: не цифра, длина 2-10 символов
                                if ticker and not ticker[0].isdigit() and 2 <= len(ticker) <= 10:
                                    tickers.add(ticker)
                        info(f"📁 Загружен {file}: {len(tickers)} тикеров")
                    except Exception as e:
                        warning(f"⚠️ Ошибка чтения {file}: {e}")

        self._pre_market_tickers = sorted(tickers)
        self._tickers_loaded = True

        info(f"📊 Загружено {len(self._pre_market_tickers)} тикеров для pre-market")
        if self._pre_market_tickers:
            info(f"   Примеры: {', '.join(self._pre_market_tickers[:10])}")

        return self._pre_market_tickers

    # ========== ДИНАМИЧЕСКИЕ НАСТРОЙКИ ==========

    def _update_dynamic_params(self, total_capital: float):
        """Обновление динамических параметров под капитал"""
        # Размер позиции (от капитала) - ДИНАМИЧЕСКИЙ РАСЧЁТ
        if total_capital < 5000:
            self.max_capital_per_order = max(200, total_capital * 0.1)
        elif total_capital < 20000:
            self.max_capital_per_order = max(500, total_capital * 0.08)
        elif total_capital < 50000:
            self.max_capital_per_order = max(1000, total_capital * 0.06)
        else:
            self.max_capital_per_order = max(2000, total_capital * 0.05)

        # Ограничиваем максимум 20,000₽ на один ордер
        self.max_capital_per_order = min(self.max_capital_per_order, 20000)

        # Стоп-лосс и тейк-профит (чем больше капитал, тем консервативнее)
        if total_capital < 5000:
            self.dynamic_stop_loss_pct = 1.0
            self.dynamic_take_profit_pct = 2.0
            self.dynamic_trailing_stop_pct = 0.5
        elif total_capital < 20000:
            self.dynamic_stop_loss_pct = 0.8
            self.dynamic_take_profit_pct = 1.5
            self.dynamic_trailing_stop_pct = 0.4
        else:
            self.dynamic_stop_loss_pct = 0.5
            self.dynamic_take_profit_pct = 1.0
            self.dynamic_trailing_stop_pct = 0.3

        info(f"📊 Динамические параметры: TP={self.dynamic_take_profit_pct:.1f}%, "
             f"SL={self.dynamic_stop_loss_pct:.1f}%, TS={self.dynamic_trailing_stop_pct:.1f}%, "
             f"MaxOrder={self.max_capital_per_order:.0f}₽")

    # ========== АНАЛИЗ КАНДИДАТОВ (ПОЛНАЯ ВЕРСИЯ) ==========

    async def analyze_pre_market_candidates(self, limit: int = 20) -> List[Dict]:
        """
        ПОЛНЫЙ АНАЛИЗ КАНДИДАТОВ ДЛЯ PRE-MARKET
        Использует всю аналитику проекта
        """
        from trading_bot.api.tbank_client import tbank
        from trading_bot.analysis.technical_analyzer import analyzer as tech_analyzer
        from trading_bot.config import config

        # Обновляем капитал
        available, total_capital, _ = tbank.get_available_funds()
        self._update_dynamic_params(total_capital)
        config.total_capital = total_capital

        tickers = self.load_pre_market_tickers()
        if not tickers:
            warning("⚠️ Нет тикеров для pre-market анализа")
            return []

        candidates = []
        cache_key = f"analysis_{datetime.now().date()}"

        # Проверяем кэш для сегодняшнего дня
        if cache_key in self._analysis_cache:
            info(f"📦 Используем кэшированный анализ ({len(self._analysis_cache[cache_key])} кандидатов)")
            return self._analysis_cache[cache_key][:limit]

        info(f"🔍 Анализ {len(tickers)} тикеров для pre-market...")

        for ticker in tickers[:limit]:
            try:
                # Получаем FIGI
                all_shares = tbank.get_all_shares(limit=500)
                figi = None
                for stock in all_shares:
                    if stock.get('ticker') == ticker:
                        figi = stock.get('figi')
                        break

                if not figi:
                    continue

                # ========== ПОЛНЫЙ ТЕХНИЧЕСКИЙ АНАЛИЗ ==========
                analysis = await tech_analyzer.analyze_stock(
                    figi=figi,
                    name=ticker,
                    ticker=ticker,
                    is_backtest=False
                )

                if not analysis or analysis.score == 0:
                    continue

                # Пропускаем слабые сигналы
                if abs(analysis.score) < self.min_score_threshold:
                    continue

                # ========== ФУНДАМЕНТАЛЬНЫЙ АНАЛИЗ (опционально) ==========
                fundamental_data = {}
                final_score = analysis.score

                if self.use_fundamental:
                    from trading_bot.analysis.fundamental_analyzer import fundamental_analyzer
                    fund_signal = await fundamental_analyzer.analyze(
                        ticker=ticker,
                        technical_score=analysis.score
                    )
                    if fund_signal and fund_signal.impact_on_score != 0:
                        final_score += fund_signal.impact_on_score
                        fundamental_data = {
                            'action': fund_signal.action,
                            'overall_score': fund_signal.metrics.overall_score,
                            'pe_ratio': fund_signal.metrics.pe_ratio,
                            'roe': fund_signal.metrics.roe,
                            'dividend_yield': fund_signal.metrics.dividend_yield
                        }

                # ========== ОПРЕДЕЛЯЕМ НАПРАВЛЕНИЕ ==========
                if final_score >= self.min_score_threshold:
                    action = "BUY"
                    confidence = min(0.95, 0.5 + (final_score / 20))
                elif final_score <= -self.min_score_threshold:
                    action = "SELL"
                    confidence = min(0.95, 0.5 + (abs(final_score) / 20))
                else:
                    continue

                # Получаем текущую цену
                current_price = tbank.get_current_price(figi)
                if not current_price or current_price <= 0:
                    continue

                # Получаем лот
                lot = 1
                for stock in all_shares:
                    if stock.get('figi') == figi:
                        lot = stock.get('lot', 1)
                        break

                # ✅ ИСПРАВЛЕНО: используем dynamic params вместо position_sizer
                # Рассчитываем количество через dynamic params
                max_amount = min(self.max_capital_per_order, available * 0.15)
                quantity = int(max_amount / current_price)

                # Корректировка по лоту
                if lot > 1:
                    quantity = (quantity // lot) * lot
                if quantity < lot:
                    quantity = lot

                # Проверка достаточности средств
                total_cost = quantity * current_price
                if total_cost > available * 0.8:
                    warning(f"⚠️ Недостаточно средств для {ticker}: нужно {total_cost:.0f}₽, есть {available:.0f}₽")
                    continue

                candidates.append({
                    'ticker': ticker,
                    'figi': figi,
                    'action': action,
                    'confidence': confidence,
                    'price': current_price,
                    'quantity': quantity,
                    'lot': lot,
                    'total_cost': total_cost,
                    'score': analysis.score,
                    'final_score': final_score,
                    'rsi': getattr(analysis, 'rsi', None),
                    'macd': getattr(analysis, 'macd', None),
                    'volume_ratio': getattr(analysis, 'volume_ratio', None),
                    'signals': getattr(analysis, 'signals', [])[:5],
                    'recommendation': getattr(analysis, 'recommendation', ''),
                    'support_levels': getattr(analysis, 'support_levels', []),
                    'resistance_levels': getattr(analysis, 'resistance_levels', []),
                    'candle_patterns': getattr(analysis, 'candle_patterns', {}),
                    'fundamental': fundamental_data,
                    'reason': self._generate_reason(analysis, final_score, fundamental_data)
                })

                debug(f"   {ticker}: score={analysis.score} → {final_score} | {action}")

            except Exception as e:
                debug(f"Ошибка анализа {ticker}: {e}")
                continue

        # Сортируем по финальному score (от большего к меньшему)
        candidates.sort(key=lambda x: x.get('final_score', 0), reverse=True)

        # Кэшируем результат на сегодня
        self._analysis_cache[cache_key] = candidates

        if candidates:
            info(f"🎯 Найдено {len(candidates)} pre-market кандидатов:")
            for i, c in enumerate(candidates[:10], 1):
                icon = "🟢" if c['action'] == "BUY" else "🔴"
                info(f"   {i}. {icon} {c['ticker']}: {c['action']} | "
                     f"score={c['score']}→{c['final_score']} | "
                     f"цена={c['price']:.2f}₽ | "
                     f"уверенность={c['confidence']:.0%}")
        else:
            info("⚠️ Нет кандидатов для pre-market торговли")

        return candidates[:limit]

    def _generate_reason(self, analysis, final_score: int, fundamental_data: Dict) -> str:
        """Генерация понятного объяснения сигнала"""
        reasons = []

        # Технические причины
        if analysis.rsi:
            if analysis.rsi < 35:
                reasons.append(f"RSI={analysis.rsi:.0f} (перепроданность)")
            elif analysis.rsi > 65:
                reasons.append(f"RSI={analysis.rsi:.0f} (перекупленность)")

        if analysis.macd and abs(analysis.macd) > 0.1:
            reasons.append(f"MACD={analysis.macd:.2f}")

        if analysis.volume_ratio and analysis.volume_ratio > 1.2:
            reasons.append(f"Объём {analysis.volume_ratio:.1f}x")

        # Свечные паттерны
        if analysis.candle_patterns:
            for pattern in list(analysis.candle_patterns.keys())[:2]:
                if pattern in ['hammer', 'bullish_engulfing']:
                    reasons.append("Бычий свечной паттерн")
                elif pattern in ['hanging_man', 'bearish_engulfing']:
                    reasons.append("Медвежий свечной паттерн")

        # Фундаментальные причины
        if fundamental_data:
            if fundamental_data.get('action') in ['STRONG_BUY', 'BUY']:
                reasons.append(f"Фундаментально: {fundamental_data.get('action')}")
            elif fundamental_data.get('action') in ['STRONG_SELL', 'SELL']:
                reasons.append(f"Фундаментально: {fundamental_data.get('action')}")

        if not reasons:
            reasons.append(f"Score={final_score}")

        return f"{' | '.join(reasons[:4])}"

    # ========== СОЗДАНИЕ ПЛАНА ==========

    async def create_pre_market_plan(self) -> List[PreMarketOrder]:
        """Создание pre-market плана на день"""
        info(f"\n{'═' * 55}")
        info(f"🌅 СОЗДАНИЕ PRE-MARKET ПЛАНА")
        info(f"   Время: {datetime.now(MOSCOW_TZ).strftime('%H:%M:%S')} МСК")
        info(f"   Дата: {datetime.now(MOSCOW_TZ).strftime('%Y-%m-%d')}")
        info(f"{'═' * 55}")

        # Получаем баланс
        from trading_bot.api.tbank_client import tbank
        available, total_capital, _ = tbank.get_available_funds()

        if available < 500:
            warning(f"⚠️ Недостаточно средств для pre-market: {available:.0f}₽")
            return []

        # Обновляем динамические параметры
        self._update_dynamic_params(total_capital)

        # Анализируем кандидатов
        candidates = await self.analyze_pre_market_candidates(limit=self.max_orders_per_day * 2)

        if not candidates:
            warning("⚠️ Нет кандидатов для pre-market")
            return []

        orders = []
        total_allocated = 0

        for cand in candidates:
            # Проверяем лимит ордеров
            if len(orders) >= self.max_orders_per_day:
                info(f"⏸️ Достигнут лимит ордеров ({self.max_orders_per_day})")
                break

            # Проверяем лимит капитала
            if total_allocated + cand['total_cost'] > available * 0.5:
                info(f"⏸️ Достигнут лимит капитала ({total_allocated:.0f}₽)")
                break

            # Цена для лимитного ордера (чуть лучше рынка для гарантии исполнения)
            if cand['action'] == "BUY":
                # Для покупки ставим цену чуть выше рынка
                limit_price = round(cand['price'] * 1.002, 4)
            else:
                # Для продажи ставим цену чуть ниже рынка
                limit_price = round(cand['price'] * 0.998, 4)

            # Стоп-лосс и тейк-профит (динамические)
            if cand['action'] == "BUY":
                stop_loss_price = round(limit_price * (1 - self.dynamic_stop_loss_pct / 100), 4)
                take_profit_price = round(limit_price * (1 + self.dynamic_take_profit_pct / 100), 4)
            else:
                stop_loss_price = round(limit_price * (1 + self.dynamic_stop_loss_pct / 100), 4)
                take_profit_price = round(limit_price * (1 - self.dynamic_take_profit_pct / 100), 4)

            # Время истечения (конец торгового дня)
            expires_at = datetime.now(MOSCOW_TZ).replace(
                hour=18, minute=59, second=59, microsecond=0
            )
            # Если вечерняя сессия, то позже
            if self.is_evening_session():
                expires_at = datetime.now(MOSCOW_TZ).replace(
                    hour=23, minute=49, second=59, microsecond=0
                )

            # Генерируем ID ордера
            order_id = f"PM_{cand['ticker']}_{datetime.now().strftime('%H%M%S')}"

            order = PreMarketOrder(
                order_id=order_id,
                ticker=cand['ticker'],
                figi=cand['figi'],
                direction=cand['action'],
                quantity=cand['quantity'],
                limit_price=limit_price,
                confidence=cand['confidence'],
                score=cand['score'],
                final_score=cand['final_score'],
                reason=cand['reason'],
                created_at=datetime.now(MOSCOW_TZ),
                expires_at=expires_at,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                entry_price=cand['price'],
                analysis_data={
                    'rsi': cand.get('rsi'),
                    'macd': cand.get('macd'),
                    'signals': cand.get('signals', []),
                    'support_levels': cand.get('support_levels', []),
                    'resistance_levels': cand.get('resistance_levels', []),
                    'candle_patterns': cand.get('candle_patterns', {}),
                    'fundamental': cand.get('fundamental', {})
                }
            )

            orders.append(order)
            total_allocated += cand['total_cost']

            info(f"📝 {cand['action']} {cand['ticker']}: "
                 f"{cand['quantity']}шт по {limit_price:.2f}₽ | "
                 f"сумма={cand['total_cost']:.0f}₽ | "
                 f"score={cand['score']}→{cand['final_score']} | "
                 f"уверенность={cand['confidence']:.0%}")
            debug(f"      Причина: {cand['reason']}")

        self.pending_orders = orders

        info(f"\n📊 ИТОГО: {len(orders)} ордеров на сумму {total_allocated:.0f}₽")
        info(f"{'═' * 55}")

        return orders

    # ========== РАЗМЕЩЕНИЕ ОРДЕРОВ ==========

    async def place_pre_market_orders(self) -> int:
        """Размещение pre-market ордеров"""
        from trading_bot.api.tbank_client import tbank

        if not self.pending_orders:
            warning("⚠️ Нет pending ордеров для размещения")
            return 0

        info(f"\n📤 РАЗМЕЩЕНИЕ PRE-MARKET ОРДЕРОВ ({len(self.pending_orders)} шт)")

        placed = 0
        session = self.get_current_session()

        for order in self.pending_orders:
            try:
                info(f"\n🔄 {order.direction} {order.ticker}: "
                     f"{order.quantity}шт по {order.limit_price:.2f}₽")

                # В pre-market используем лимитные заявки
                # В основную сессию можно использовать рыночные
                use_market = session == TradingSession.MAIN

                if order.direction == "BUY":
                    if use_market:
                        success_flag = tbank.buy(order.figi, order.quantity, use_market=True)
                    else:
                        success_flag = tbank.buy(order.figi, order.quantity, use_market=False)
                else:
                    if use_market:
                        success_flag = tbank.sell(order.figi, order.quantity, use_market=True)
                    else:
                        success_flag = tbank.sell(order.figi, order.quantity, use_market=False)

                if success_flag:
                    order.status = "ACTIVE"
                    self.active_orders.append(order)
                    placed += 1
                    success(f"✅ Ордер {order.ticker} размещён")
                else:
                    order.status = "REJECTED"
                    self.rejected_orders.append(order)
                    warning(f"⚠️ Ордер {order.ticker} не размещён")

            except Exception as e:
                error(f"❌ Ошибка размещения {order.ticker}: {e}")
                order.status = "ERROR"
                self.rejected_orders.append(order)

        # Очищаем pending
        self.pending_orders = [o for o in self.pending_orders if o.status == "PENDING"]

        info(f"\n📊 Размещено ордеров: {placed}/{len(self.active_orders)}")
        return placed

    # ========== МОНИТОРИНГ ОРДЕРОВ ==========

    async def monitor_orders(self):
        """Мониторинг активных ордеров - с правильной обработкой остановки"""
        from trading_bot.api.tbank_client import tbank

        info("🔍 Запуск мониторинга pre-market ордеров")

        while self._running:
            try:
                # Проверяем, не остановлен ли бот
                if hasattr(self.bot, '_shutting_down') and self.bot._shutting_down:
                    info("🛑 Бот останавливается, завершаем мониторинг")
                    break

                now = datetime.now(MOSCOW_TZ)
                session = self.get_current_session()

                # Проверяем истекшие ордера
                expired = []
                for order in self.active_orders:
                    if now > order.expires_at:
                        expired.append(order)
                        info(f"⏰ Истёк ордер {order.ticker}")
                        order.status = "EXPIRED"

                for order in expired:
                    if order in self.active_orders:
                        self.active_orders.remove(order)

                # Проверяем исполненные
                for order in self.active_orders[:]:
                    try:
                        current_price = tbank.get_current_price(order.figi)
                        if current_price:
                            if order.direction == "BUY":
                                if current_price <= order.limit_price:
                                    order.status = "FILLED"
                                    order.updated_at = datetime.now(MOSCOW_TZ)
                                    self.filled_orders.append(order)
                                    if order in self.active_orders:
                                        self.active_orders.remove(order)
                                    success(
                                        f"✅ ИСПОЛНЕН: {order.ticker} | {order.quantity}шт по ~{current_price:.2f}₽ | "
                                        f"ожидалось {order.limit_price:.2f}₽")
                                    # Отправляем уведомление в Telegram
                                    from trading_bot.telegram.telegram_notifier import get_telegram_notifier
                                    telegram = get_telegram_notifier()
                                    if telegram:
                                        telegram.send_message(
                                            f"✅ PRE-MARKET ИСПОЛНЕН\n"
                                            f"{order.direction} {order.ticker}\n"
                                            f"{order.quantity}шт по {current_price:.2f}₽\n"
                                            f"Score: {order.score}→{order.final_score}\n"
                                            f"Причина: {order.reason[:100]}"
                                        )
                            else:  # SELL
                                if current_price >= order.limit_price:
                                    order.status = "FILLED"
                                    order.updated_at = datetime.now(MOSCOW_TZ)
                                    self.filled_orders.append(order)
                                    if order in self.active_orders:
                                        self.active_orders.remove(order)
                                    success(
                                        f"✅ ИСПОЛНЕН: {order.ticker} | {order.quantity}шт по ~{current_price:.2f}₽ | "
                                        f"ожидалось {order.limit_price:.2f}₽")
                    except Exception as e:
                        debug(f"Ошибка проверки ордера {order.ticker}: {e}")

                # Очистка кэша анализа в начале нового дня
                if session == TradingSession.PRE_MARKET and now.hour == self.pre_market_start_hour and now.minute < 10:
                    self._analysis_cache.clear()
                    info("🧹 Кэш анализа очищен (новый день)")

                # Используем asyncio.sleep с проверкой флага (не блокируем остановку)
                for _ in range(10):  # 10 * 1 секунда = 10 секунд
                    if not self._running:
                        break
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                info("🛑 Мониторинг pre-market ордеров отменён")
                break
            except Exception as e:
                error(f"❌ Ошибка мониторинга: {e}")
                if self._running:
                    await asyncio.sleep(5)

        info("🔍 Мониторинг pre-market ордеров завершён")

    # ========== ЗАПУСК И ОСТАНОВКА ==========

    async def start_trader(self):
        """Запуск PreMarketTrader"""
        if self._running:
            return

        # ========== ✅ ПРОВЕРКА API ПЕРЕД ЗАПУСКОМ ==========
        from trading_bot.api.tbank_client import tbank

        # Проверяем API через прямой вызов
        try:
            # Пытаемся получить капитал - если получится, API работает
            available, total_capital, _ = tbank.get_available_funds()
            if available is None or total_capital is None:
                raise Exception("Не удалось получить капитал")
            info(f"✅ API проверен: капитал {total_capital:.0f}₽")
        except Exception as e:
            warning(f"⚠️ API Т-Банка недоступен: {e}")
            warning("   PreMarketTrader не запущен")
            return

        self._running = True
        info("🌅 PreMarketTrader запущен (ПОЛНАЯ ВЕРСИЯ)")

        # Создаём план
        await self.create_pre_market_plan()

        # Размещаем ордера
        await self.place_pre_market_orders()

        # Запускаем фоновый мониторинг и СОХРАНЯЕМ задачу
        self._monitor_task = asyncio.create_task(self.monitor_orders())

    async def stop_trader(self):
        """Остановка PreMarketTrader"""
        info("🌙 Остановка PreMarketTrader...")
        self._running = False

        # Отменяем задачу мониторинга
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            info("   ✅ Задача мониторинга отменена")

        info("🌙 PreMarketTrader остановлен")

    def get_stats(self) -> Dict[str, Any]:
        """Статистика"""
        return {
            "pending_orders": len(self.pending_orders),
            "active_orders": len(self.active_orders),
            "filled_orders": len(self.filled_orders),
            "rejected_orders": len(self.rejected_orders),
            "current_session": self.get_current_session().value,
            "tickers_loaded": len(self._pre_market_tickers),
            "max_orders_per_day": self.max_orders_per_day,
            "max_capital_per_order": self.max_capital_per_order,
            "min_score_threshold": self.min_score_threshold,
            "dynamic_stop_loss_pct": self.dynamic_stop_loss_pct,
            "dynamic_take_profit_pct": self.dynamic_take_profit_pct,
            "use_fundamental": self.use_fundamental,
            "cache_hit_rate": len(self._analysis_cache) > 0
        }

    def get_filled_orders_summary(self) -> List[Dict]:
        """Сводка по исполненным ордерам"""
        return [
            {
                'order_id': o.order_id,
                'ticker': o.ticker,
                'direction': o.direction,
                'quantity': o.quantity,
                'limit_price': o.limit_price,
                'entry_price': o.entry_price,
                'confidence': o.confidence,
                'score': o.score,
                'final_score': o.final_score,
                'reason': o.reason,
                'created_at': o.created_at.isoformat(),
                'filled_at': o.updated_at if hasattr(o, 'updated_at') else None
            }
            for o in self.filled_orders
        ]
