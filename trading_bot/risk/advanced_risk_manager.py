#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Advanced Risk Manager - Продвинутое управление капиталом
- Kelly Criterion (расчёт оптимального размера позиции)
- VaR (Value at Risk) - оценка максимальных потерь
- Корреляционный анализ портфеля
- Динамический риск на основе волатильности
"""

import math
import numpy as np
from trading_bot.cache import TTLCache
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque

from trading_bot.logger import info, success, error, warning, debug


@dataclass
class TradeRecord:
    """Запись о завершённой сделке"""
    ticker: str
    side: str  # LONG / SHORT
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    entry_time: datetime
    exit_time: datetime
    holding_minutes: float


@dataclass
class RiskMetrics:
    """Метрики риска для позиции/портфеля"""
    optimal_position_pct: float  # Оптимальный размер позиции по Келли
    kelly_fraction: float  # Дробь Келли (0-1)
    var_95: float  # 95% VaR в процентах
    var_99: float  # 99% VaR в процентах
    expected_sharpe: float  # Ожидаемый Sharpe ratio
    correlation_risk: float  # Риск корреляции (0-1)
    adjusted_position_pct: float  # Скорректированный размер позиции


class AdvancedRiskManager:
    """
    Продвинутый менеджер рисков
    Использует:
    - Kelly Criterion для расчёта оптимального размера позиции
    - Historical VaR для оценки максимальных потерь
    - Корреляционный анализ для диверсификации
    """

    def __init__(self, max_trade_history: int = 100):
        # Кэш для корреляций (TTL 1 час)
        self._correlation_cache = TTLCache(default_ttl=3600, max_size=500, name="correlation_cache")

        self.trade_history: deque = deque(maxlen=max_trade_history)
        self.position_correlations: Dict[str, Dict[str, float]] = {}
        self.daily_returns: Dict[str, deque] = {}

        # Параметры Келли
        self.kelly_fraction: float = 0.25
        self.min_kelly_position: float = 0.02
        self.max_kelly_position: float = 0.15

        # Параметры VaR
        self.var_confidence: float = 0.95
        self.var_lookback_days: int = 30

        # Пороги корреляции
        self.correlation_threshold: float = 0.7
        self.sector_penalty: float = 0.3  # ✅ УЖЕ ЕСТЬ - штраф за один сектор

        info("📊 AdvancedRiskManager инициализирован")
        info(f"   📐 Kelly fraction: {self.kelly_fraction * 100:.0f}% от полного")
        info(f"   🎯 Размер позиции: {self.min_kelly_position * 100:.0f}%-{self.max_kelly_position * 100:.0f}%")
        info(f"   📉 VaR уверенность: {self.var_confidence * 100:.0f}%")

    # ========================================================================
    # 1. KELLY CRITERION
    # ========================================================================

    def calculate_kelly_fraction(
            self,
            win_rate: float,
            avg_win: float,
            avg_loss: float
    ) -> float:
        """
        Расчёт оптимальной доли капитала по формуле Келли

        Формула: f* = (p * b - q) / b
        где:
        p = вероятность выигрыша (win_rate)
        q = вероятность проигрыша (1 - p)
        b = соотношение средней прибыли к среднему убытку (avg_win / abs(avg_loss))

        Args:
            win_rate: Доля успешных сделок (0-1)
            avg_win: Средняя прибыль в процентах (положительная)
            avg_loss: Средний убыток в процентах (отрицательный)

        Returns:
            Оптимальная доля капитала (0-1)
        """
        if avg_loss >= 0:
            warning("⚠️ Нет убыточных сделок, используем максимальный риск")
            return self.max_kelly_position

        b = abs(avg_win / avg_loss) if avg_loss != 0 else 1
        q = 1 - win_rate

        kelly = (win_rate * b - q) / b

        # Ограничиваем и применяем дробь Келли
        kelly = max(0, min(1, kelly)) * self.kelly_fraction

        # Ограничиваем диапазоном
        kelly = max(self.min_kelly_position, min(self.max_kelly_position, kelly))

        debug(f"📐 Kelly: win_rate={win_rate:.2f}, b={b:.2f}, raw={kelly / self.kelly_fraction:.3f}, final={kelly:.3f}")
        return kelly

    def calculate_kelly_from_history(self) -> float:
        """
        Расчёт Келли на основе истории сделок
        """
        if len(self.trade_history) < 10:
            warning(f"⚠️ Недостаточно сделок для Келли ({len(self.trade_history)}/10)")
            return self.min_kelly_position

        wins = [t for t in self.trade_history if t.pnl > 0]
        losses = [t for t in self.trade_history if t.pnl <= 0]

        win_rate = len(wins) / len(self.trade_history)
        avg_win = abs(sum(t.pnl_pct for t in wins) / len(wins)) if wins else 0
        avg_loss = abs(sum(t.pnl_pct for t in losses) / len(losses)) if losses else 0.01

        return self.calculate_kelly_fraction(win_rate, avg_win, avg_loss)

    # ========================================================================
    # 2. VALUE AT RISK (VaR)
    # ========================================================================

    def calculate_historical_var(
            self,
            returns: List[float],
            confidence: float = 0.95
    ) -> float:
        """
        Расчёт Historical VaR

        Args:
            returns: Список дневных доходностей (%)
            confidence: Уровень уверенности (0.95 = 95%)

        Returns:
            VaR в процентах (положительное число = максимальные потери)
        """
        if len(returns) < 10:
            return 5.0  # Дефолтный VaR 5%

        sorted_returns = sorted(returns)
        index = int((1 - confidence) * len(sorted_returns))

        var = abs(sorted_returns[index])
        debug(f"📉 VaR {confidence * 100:.0f}%: {var:.2f}%")
        return var

    def calculate_portfolio_var(
            self,
            positions: List[Dict],
            correlations: Dict[str, Dict[str, float]] = None
    ) -> float:
        """
        Расчёт VaR для портфеля с учётом корреляций

        Args:
            positions: Список позиций с весами и VaR
            correlations: Матрица корреляций между инструментами

        Returns:
            Портфельный VaR в процентах
        """
        if not positions:
            return 0

        weights = [p.get('weight', 1 / len(positions)) for p in positions]
        vars_list = [p.get('var', 5.0) for p in positions]

        # Если нет корреляций, используем простую сумму
        if not correlations or len(positions) == 1:
            portfolio_var = sum(w * v for w, v in zip(weights, vars_list))
        else:
            # Упрощённый расчёт с учётом корреляций
            # В реальности нужна матрица ковариаций
            avg_correlation = 0.5
            diversification_benefit = 1 - avg_correlation * (1 - 1 / len(positions))
            portfolio_var = sum(w * v for w, v in zip(weights, vars_list)) * diversification_benefit

        debug(f"📊 Портфельный VaR: {portfolio_var:.2f}%")
        return portfolio_var

    # ========================================================================
    # 3. КОРРЕЛЯЦИОННЫЙ АНАЛИЗ
    # ========================================================================

    def calculate_correlation(
            self,
            returns1: List[float],
            returns2: List[float]
    ) -> float:
        """
        Расчёт корреляции Пирсона между двумя рядами доходностей
        """
        if len(returns1) < 5 or len(returns2) < 5:
            return 0.5  # Дефолтная корреляция

        try:
            correlation = np.corrcoef(returns1, returns2)[0, 1]
            return max(-1, min(1, correlation))
        except Exception as e:
            debug(f"Ошибка расчёта корреляции: {e}")
            return 0.5

    def get_correlation_penalty(
            self,
            ticker: str,
            open_positions: List[str],
            sector_map: Dict[str, str] = None
    ) -> float:
        """
        Расчёт штрафа за корреляцию с существующими позициями

        Args:
            ticker: Новый тикер
            open_positions: Список открытых позиций
            sector_map: Словарь {тикер: сектор}

        Returns:
            Штраф (0-1), где 0 = нет штрафа, 1 = максимальный штраф
        """
        if not open_positions:
            return 0

        # Если нет истории для расчёта корреляции
        if ticker not in self.daily_returns:
            return 0.1  # Небольшой штраф за неизвестность

        max_correlation = 0
        same_sector_count = 0

        for pos in open_positions:
            if pos not in self.daily_returns:
                continue

            corr = self.calculate_correlation(
                list(self.daily_returns[ticker]),
                list(self.daily_returns[pos])
            )
            max_correlation = max(max_correlation, abs(corr))

            # Проверка сектора
            if sector_map and ticker in sector_map and pos in sector_map:
                if sector_map[ticker] == sector_map[pos]:
                    same_sector_count += 1

        # Штраф за высокую корреляцию
        correlation_penalty = max_correlation * 0.5

        # Штраф за концентрацию в одном секторе
        sector_penalty = min(1, same_sector_count * self.sector_penalty)

        total_penalty = min(1, correlation_penalty + sector_penalty)

        debug(f"📊 Корреляция {ticker}: max_corr={max_correlation:.2f}, penalty={total_penalty:.2f}")
        return total_penalty

    # ========================================================================
    # 4. ДИНАМИЧЕСКИЙ РАЗМЕР ПОЗИЦИИ
    # ========================================================================

    def calculate_position_size(
            self,
            capital: float,
            ticker: str,
            open_positions: List[str],
            sector_map: Dict[str, str] = None,
            volatility: float = None,
            force_min_size: bool = False
    ) -> Dict[str, Any]:
        """
        Расчёт оптимального размера позиции с учётом всех факторов

        Args:
            capital: Текущий капитал
            ticker: Тикер для анализа
            open_positions: Список открытых позиций
            sector_map: Словарь секторов
            volatility: Текущая волатильность (опционально)
            force_min_size: Принудительно использовать минимальный размер

        Returns:
            Словарь с параметрами позиции
        """
        from trading_bot.core.settings_manager import settings_manager

        # 1. Базовый размер по Келли
        kelly_size = self.calculate_kelly_from_history()

        # 2. Корректировка на волатильность
        vol_penalty = 0
        if volatility:
            vol_penalty = min(1, volatility / 0.03)  # 3% волатильность = база
            kelly_size = kelly_size * (1 - vol_penalty * 0.5)

        # 3. Корректировка на корреляцию (ТОЛЬКО ЕСЛИ ВКЛЮЧЕНО)
        correlation_penalty = 0
        if settings_manager.get('correlation_analysis', False):
            correlation_penalty = self.get_correlation_penalty(
                ticker, open_positions, sector_map
            )
            kelly_size = kelly_size * (1 - correlation_penalty)
            debug(f"📊 Корреляционный штраф для {ticker}: {correlation_penalty:.2f}")

        # 4. Корректировка на количество открытых позиций
        max_positions = settings_manager.get('max_positions', 5)
        position_count_penalty = len(open_positions) / max_positions
        kelly_size = kelly_size * (1 - position_count_penalty * 0.3)

        # 5. Итоговый размер в процентах
        final_pct = max(
            self.min_kelly_position,
            min(self.max_kelly_position, kelly_size)
        )

        if force_min_size:
            final_pct = self.min_kelly_position

        # 6. Расчёт суммы в рублях
        amount = capital * final_pct

        # 7. Расчёт VaR для этой позиции
        var_95 = self.calculate_historical_var(
            list(self.daily_returns.get(ticker, [])),
            self.var_confidence
        )
        var_amount = amount * var_95 / 100

        return {
            'position_pct': final_pct,
            'position_amount': amount,
            'kelly_pct': kelly_size,
            'correlation_penalty': correlation_penalty,
            'var_95_pct': var_95,
            'var_95_amount': var_amount,
            'volatility_penalty': vol_penalty if volatility else 0,
            'position_count_penalty': position_count_penalty,
        }

    # ========================================================================
    # 5. УПРАВЛЕНИЕ ИСТОРИЕЙ
    # ========================================================================

    def add_trade(self, trade: TradeRecord):
        """Добавление завершённой сделки в историю"""
        self.trade_history.append(trade)

        # Обновляем дневные доходности для корреляций
        if trade.ticker not in self.daily_returns:
            self.daily_returns[trade.ticker] = deque(maxlen=self.var_lookback_days)

        self.daily_returns[trade.ticker].append(trade.pnl_pct)

        info(f"📊 Добавлена сделка {trade.ticker}: P&L={trade.pnl_pct:.2f}%, история={len(self.trade_history)}")

    def get_stats(self) -> Dict[str, Any]:
        """Получение статистики Risk Manager"""
        if len(self.trade_history) == 0:
            return {'total_trades': 0, 'message': 'Нет истории сделок'}

        wins = [t for t in self.trade_history if t.pnl > 0]
        losses = [t for t in self.trade_history if t.pnl <= 0]

        return {
            'total_trades': len(self.trade_history),
            'win_count': len(wins),
            'loss_count': len(losses),
            'win_rate': len(wins) / len(self.trade_history),
            'avg_win_pct': sum(t.pnl_pct for t in wins) / len(wins) if wins else 0,
            'avg_loss_pct': sum(t.pnl_pct for t in losses) / len(losses) if losses else 0,
            'profit_factor': abs(sum(t.pnl for t in wins) / sum(t.pnl for t in losses)) if losses else float('inf'),
            'kelly_position': self.calculate_kelly_from_history(),
            'tracked_tickers': len(self.daily_returns),
        }

    def generate_risk_report(self) -> str:
        """Генерация отчёта по рискам"""
        stats = self.get_stats()

        report = []
        report.append("=" * 60)
        report.append("📊 ОТЧЁТ ПО РИСКАМ")
        report.append("=" * 60)

        if stats.get('total_trades', 0) == 0:
            report.append("   Нет истории сделок для анализа")
        else:
            report.append(f"   📈 Всего сделок: {stats['total_trades']}")
            report.append(f"   ✅ Успешных: {stats['win_count']} ({stats['win_rate'] * 100:.1f}%)")
            report.append(f"   ❌ Убыточных: {stats['loss_count']}")
            report.append(f"   📊 Средняя прибыль: {stats['avg_win_pct']:.2f}%")
            report.append(f"   📉 Средний убыток: {stats['avg_loss_pct']:.2f}%")
            report.append(f"   🎯 Profit Factor: {stats['profit_factor']:.2f}")
            report.append(f"   📐 Рекомендуемый риск на сделку: {stats['kelly_position'] * 100:.1f}%")

        report.append(f"   📊 Отслеживается тикеров: {stats.get('tracked_tickers', 0)}")
        report.append("=" * 60)

        return "\n".join(report)

    def calculate_correlation_cached(self, ticker1: str, ticker2: str) -> float:
        """Расчёт корреляции с кэшированием"""
        cache_key = f"corr_{ticker1}_{ticker2}"

        cached = self._correlation_cache.get(cache_key)
        if cached is not None:
            debug(f"📦 Cache hit for correlation {ticker1}-{ticker2}: {cached:.2f}")
            return cached

        if ticker1 not in self.daily_returns or ticker2 not in self.daily_returns:
            return 0.5

        correlation = self.calculate_correlation(
            list(self.daily_returns[ticker1]),
            list(self.daily_returns[ticker2])
        )

        self._correlation_cache.set(cache_key, correlation, ttl=3600)
        return correlation


# Глобальный экземпляр
advanced_risk_manager = AdvancedRiskManager()

