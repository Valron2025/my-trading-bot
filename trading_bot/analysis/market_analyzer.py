#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Модуль анализа рыночных условий - ПОЛНАЯ АВТОМАТИЗАЦИЯ
   ВСЕ параметры рассчитываются автоматически на основе:
   - Текущего капитала
   - Рыночной волатильности
   - Тренда
   - Объёмов торгов
"""

from typing import Dict, Any, Tuple
from enum import Enum

from trading_bot.models import MarketConditions
from trading_bot.logger import success, warning


def _get_tbank():
    """Получение экземпляра T-Bank клиента (для избежания циклических импортов)"""
    from trading_bot.api.tbank_client import tbank
    return tbank


# ========== 1. ОПРЕДЕЛЕНИЕ СТИЛЕЙ ТОРГОВЛИ ==========

class TradingStyle(Enum):
    """Стиль торговли - автоматически выбирается"""
    SCALPING = "SCALPING"      # Очень короткие позиции (минуты)
    DAY = "DAY"                # Дневная торговля (часы)
    SWING = "SWING"            # Среднесрочная (дни)
    POSITION = "POSITION"      # Долгосрочная (недели+)

    @property
    def emoji(self) -> str:
        return {
            TradingStyle.SCALPING: "⚡",
            TradingStyle.DAY: "☀️",
            TradingStyle.SWING: "🔄",
            TradingStyle.POSITION: "🏦"
        }.get(self, "🎯")


# ========== 2. ПАРАМЕТРЫ ДЛЯ КАЖДОГО СТИЛЯ ==========

STYLE_PARAMS = {
    TradingStyle.SCALPING: {
        'take_profit_min': 0.5,
        'take_profit_max': 1.2,
        'stop_loss_factor': 0.5,
        'trailing_factor': 0.4,
        'timeout_minutes': 10,
        'max_hold_minutes': 30,
        'min_confidence': 1,
        'cycle_seconds': 8,
    },
    TradingStyle.DAY: {
        'take_profit_min': 1.0,
        'take_profit_max': 2.0,
        'stop_loss_factor': 0.6,
        'trailing_factor': 0.5,
        'timeout_minutes': 60,
        'max_hold_minutes': 240,
        'min_confidence': 2,
        'cycle_seconds': 15,
    },
    TradingStyle.SWING: {
        'take_profit_min': 2.0,
        'take_profit_max': 5.0,
        'stop_loss_factor': 0.7,
        'trailing_factor': 0.6,
        'timeout_minutes': 240,
        'max_hold_minutes': 720,
        'min_confidence': 3,
        'cycle_seconds': 30,
    },
    TradingStyle.POSITION: {
        'take_profit_min': 5.0,
        'take_profit_max': 15.0,
        'stop_loss_factor': 0.8,
        'trailing_factor': 0.7,
        'timeout_minutes': 1440,
        'max_hold_minutes': 10080,
        'min_confidence': 4,
        'cycle_seconds': 60,
    }
}


class MarketAnalyzer:
    """Анализ рыночных условий и ПОЛНАЯ АВТОМАТИЧЕСКАЯ настройка"""

    # Базовые параметры для разных уровней капитала
    CAPITAL_TIERS = {
        'micro': {'max': 3000, 'position_pct': 0.06, 'max_positions': 1, 'min_confidence': 2,
                  'take_profit': 1.5, 'stop_loss': 0.8, 'timeout': 20, 'cycle': 8},
        'small': {'max': 10000, 'position_pct': 0.08, 'max_positions': 2, 'min_confidence': 1,
                  'take_profit': 1.2, 'stop_loss': 0.6, 'timeout': 15, 'cycle': 10},
        'medium': {'max': 50000, 'position_pct': 0.10, 'max_positions': 3, 'min_confidence': 1,
                   'take_profit': 1.0, 'stop_loss': 0.5, 'timeout': 12, 'cycle': 12},
        'large': {'max': float('inf'), 'position_pct': 0.12, 'max_positions': 4, 'min_confidence': 2,
                  'take_profit': 0.8, 'stop_loss': 0.4, 'timeout': 10, 'cycle': 15}
    }

    # Множители волатильности
    VOLATILITY_MULTIPLIERS = {
        'high': {'min': 0.015, 'position': 0.6, 'take': 1.2, 'cycle': 15},
        'medium': {'min': 0.01, 'position': 0.8, 'take': 1.1, 'cycle': 12},
        'low': {'min': 0.005, 'position': 1.0, 'take': 1.0, 'cycle': 10},
        'very_low': {'min': 0, 'position': 1.2, 'take': 0.9, 'cycle': 8}
    }

    # Множители тренда
    TREND_MULTIPLIERS = {
        'strong_up': {'min': 0.3, 'take': 1.2, 'timeout': 1.2},
        'strong_down': {'min': -0.3, 'take': 0.8, 'timeout': 0.8},
        'sideways': {'min': -0.3, 'max': 0.3, 'take': 1.0, 'timeout': 1.0}
    }

    def __init__(self):
        self.otc_timeout_multiplier = 2.5

    # ========== МЕТОДЫ АНАЛИЗА РЫНКА ==========

    def analyze_market_conditions(self) -> MarketConditions:
        """Анализ текущих рыночных условий с автоматическим определением"""
        try:
            all_shares = _get_tbank().get_all_shares(limit=500)
            volatilities = []
            trends = []
            volumes = []

            success("📊 Анализ рыночных условий...")

            count = 0
            for stock_data in all_shares:
                if not stock_data.get('api_trade_available', False) or stock_data.get('currency') != "rub":
                    continue
                if count >= 50:
                    break

                try:
                    candles = _get_tbank().get_candles(stock_data['figi'], days=1, interval_minutes=5)

                    if len(candles) >= 12:
                        prices = [c[0] for c in candles]

                        # Волатильность
                        returns = []
                        for i in range(1, len(prices)):
                            if prices[i - 1] > 0:
                                returns.append((prices[i] - prices[i - 1]) / prices[i - 1])
                        if returns:
                            volatility = sum(abs(r) for r in returns) / len(returns)
                            volatilities.append(volatility)

                        # Тренд
                        recent_avg = sum(prices[-3:]) / 3 if len(prices) >= 3 else prices[-1]
                        older_avg = sum(prices[-9:-6]) / 3 if len(prices) >= 9 else prices[0]
                        if recent_avg > older_avg * 1.002:
                            trends.append(1)
                        elif recent_avg < older_avg * 0.998:
                            trends.append(-1)
                        else:
                            trends.append(0)

                        # Объёмы
                        vols = [c[1] for c in candles[-12:]]
                        if vols:
                            volume_trend = vols[-1] / sum(vols[:-1]) * len(vols[:-1]) if sum(vols[:-1]) > 0 else 1
                            volumes.append(volume_trend)

                        count += 1
                except Exception:
                    continue

            avg_volatility = sum(volatilities) / len(volatilities) if volatilities else 0.008
            avg_trend = sum(trends) / len(trends) if trends else 0
            avg_volume_trend = sum(volumes) / len(volumes) if volumes else 1.0

            # Определение типа рынка
            if avg_volatility > 0.015:
                market_type = "ВЫСОКАЯ ВОЛАТИЛЬНОСТЬ"
                risk_factor = 0.6
                vol_level = 'high'
            elif avg_volatility > 0.01:
                market_type = "СРЕДНЯЯ ВОЛАТИЛЬНОСТЬ"
                risk_factor = 0.8
                vol_level = 'medium'
            elif avg_volatility > 0.005:
                market_type = "НИЗКАЯ ВОЛАТИЛЬНОСТЬ"
                risk_factor = 1.0
                vol_level = 'low'
            else:
                market_type = "ОЧЕНЬ НИЗКАЯ ВОЛАТИЛЬНОСТЬ"
                risk_factor = 1.2
                vol_level = 'very_low'

            if avg_trend > 0.3:
                trend_direction = "БЫЧИЙ ТРЕНД"
                trend_level = 'strong_up'
            elif avg_trend < -0.3:
                trend_direction = "МЕДВЕЖИЙ ТРЕНД"
                trend_level = 'strong_down'
            else:
                trend_direction = "БОКОВИК"
                trend_level = 'sideways'

            success(f"   📈 Тип рынка: {market_type} ({avg_volatility * 100:.2f}%)")
            success(f"   📊 Тренд: {trend_direction}")
            success(f"   💰 Объёмы: {avg_volume_trend:.2f}x от нормы")

            return MarketConditions(
                volatility=avg_volatility,
                spread=0.002,
                volume_trend=avg_volume_trend,
                trend=avg_trend,
                market_type=market_type,
                trend_direction=trend_direction,
                risk_factor=risk_factor,
                vol_level=vol_level,
                trend_level=trend_level
            )

        except Exception as e:
            warning(f"Ошибка анализа рынка: {e}")
            return MarketConditions(
                volatility=0.008,
                spread=0.002,
                volume_trend=1.0,
                trend=0,
                market_type="СТАНДАРТНЫЙ",
                trend_direction="НЕЙТРАЛЬНО",
                risk_factor=1.0,
                vol_level='medium',
                trend_level='sideways'
            )

    # ========== АВТОМАТИЧЕСКИЙ ВЫБОР СТИЛЯ ==========

    def select_trading_style(self, total_capital: float, market: MarketConditions) -> Tuple[TradingStyle, str]:
        """АВТОМАТИЧЕСКИЙ ВЫБОР СТИЛЯ ТОРГОВЛИ"""
        # Защита: микро-капитал (< 10000₽)
        if total_capital < 10000:
            return TradingStyle.SCALPING, f"режим накопления (капитал {total_capital:.0f}₽ < 10000₽) — ТОРГОВЛЯ ОТКЛЮЧЕНА"

        # По капиталу
        if total_capital < 30000:
            style = TradingStyle.SCALPING
            reason = f"очень малый капитал ({total_capital:.0f}₽) — ТОЛЬКО СИЛЬНЫЕ СИГНАЛЫ"
        elif total_capital < 100000:
            style = TradingStyle.DAY
            reason = f"малый капитал ({total_capital:.0f}₽)"
        elif total_capital < 500000:
            style = TradingStyle.SWING
            reason = f"средний капитал ({total_capital:.0f}₽)"
        else:
            style = TradingStyle.POSITION
            reason = f"крупный капитал ({total_capital:.0f}₽)"

        # Корректировка по волатильности
        if market.volatility > 0.02:
            style = TradingStyle.SCALPING
            reason = f"очень высокая волатильность ({market.volatility * 100:.2f}%)"
        elif market.volatility < 0.003:
            if style == TradingStyle.SCALPING:
                style = TradingStyle.SWING
                reason = f"низкая волатильность ({market.volatility * 100:.2f}%)"

        # Корректировка по тренду
        if market.trend < -0.3 and style != TradingStyle.SCALPING:
            style = TradingStyle.SCALPING
            reason = "сильный медвежий тренд (требует быстрых выходов)"

        # Если совсем мало денег - только наблюдение
        if total_capital < 5000:
            style = TradingStyle.SCALPING
            reason = "микро-капитал — ТОЛЬКО НАБЛЮДЕНИЕ, БЕЗ ТОРГОВЛИ"

        return style, reason

    # ========== АДАПТИВНЫЙ РАСЧЁТ ПАРАМЕТРОВ ==========

    def calculate_adaptive_parameters(self, total_capital: float, market: MarketConditions) -> Dict[str, Any]:
        """ПОЛНОСТЬЮ АВТОМАТИЧЕСКИЙ РАСЧЁТ ВСЕХ ПАРАМЕТРОВ"""

        # # Защита: микро-капитал (< 5000₽)
        # if total_capital < 5000:
        #     success(f"\n🔒 МИКРО-КАПИТАЛ: {total_capital:.0f}₽ < 5000₽ — очень осторожная торговля")
        #
        #     min_trade_amount = max(300, int(total_capital * 0.15))
        #     min_trade_amount = ((min_trade_amount + 9) // 10) * 10
        #
        #     return {
        #         'trading_style': 'MICRO',
        #         'take_profit': 1 + 1.5 / 100,
        #         'stop_loss': 1 - 0.8 / 100,
        #         'trailing_stop': 0.003,
        #         'timeout_minutes': 10,
        #         'cycle_seconds': 8,
        #         'position_size_pct': 0.05,
        #         'max_positions': 1,
        #         'min_trade_amount': min_trade_amount,
        #         'min_share_price': 1,
        #         'max_share_price': 2000,
        #         'min_confidence_score': 3,
        #         'use_short': False,
        #         'short_score_threshold': -20,
        #         'long_score_threshold': 3,
        #         'short_vwap_threshold': 1.02,
        #         'short_volume_spike': 2.0,
        #         'otc_timeout_multiplier': self.otc_timeout_multiplier,
        #         'max_hold_minutes': 15,
        #     }
        #
        # # Защита: очень малый капитал (5000-10000₽)
        # if total_capital < 10000:
        #     success(f"\n🔒 МАЛЫЙ КАПИТАЛ: {total_capital:.0f}₽ — осторожная торговля")
        #
        #     min_trade_amount = max(400, int(total_capital * 0.12))
        #     min_trade_amount = ((min_trade_amount + 9) // 10) * 10
        #
        #     return {
        #         'trading_style': 'SMALL',
        #         'take_profit': 1 + 1.2 / 100,
        #         'stop_loss': 1 - 0.6 / 100,
        #         'trailing_stop': 0.004,
        #         'timeout_minutes': 15,
        #         'cycle_seconds': 10,
        #         'position_size_pct': 0.08,
        #         'max_positions': 2,
        #         'min_trade_amount': min_trade_amount,
        #         'min_share_price': 1,
        #         'max_share_price': 2000,
        #         'min_confidence_score': 2,
        #         'use_short': False,
        #         'short_score_threshold': -20,
        #         'long_score_threshold': 2,
        #         'short_vwap_threshold': 1.02,
        #         'short_volume_spike': 2.0,
        #         'otc_timeout_multiplier': self.otc_timeout_multiplier,
        #         'max_hold_minutes': 20,
        #     }

        # ========== АВТОМАТИЧЕСКИЙ ВЫБОР СТИЛЯ ==========
        trading_style, style_reason = self.select_trading_style(total_capital, market)
        style_params = STYLE_PARAMS[trading_style]

        success(f"\n🎯 АВТО-СТИЛЬ: {trading_style.value} ({style_reason})")

        # Размер позиции
        if total_capital < 30000:
            position_size_pct = 0.04
        elif total_capital < 50000:
            position_size_pct = 0.05
        elif total_capital < 100000:
            position_size_pct = 0.06
        else:
            position_size_pct = 0.08
        position_size_pct = max(0.02, min(0.15, position_size_pct))

        # Тейк-профит
        vol_mult = 1.0
        if market.volatility > 0.02:
            vol_mult = 1.3
        elif market.volatility > 0.015:
            vol_mult = 1.1
        elif market.volatility < 0.005:
            vol_mult = 0.9

        take_profit_min = style_params['take_profit_min'] * vol_mult
        take_profit_max = style_params['take_profit_max'] * vol_mult
        take_profit = (take_profit_min + take_profit_max) / 2
        take_profit = max(take_profit_min, min(take_profit_max, take_profit))

        # Стоп-лосс
        stop_loss = take_profit * style_params.get('stop_loss_factor', 0.8)
        stop_loss = max(0.5, min(2.0, stop_loss))

        # Трейлинг-стоп
        trailing_stop = stop_loss * style_params.get('trailing_factor', 0.3)
        trailing_stop = max(0.1, min(1.0, trailing_stop))

        # Порог уверенности
        if total_capital < 20000:
            min_confidence = 4
        elif total_capital < 50000:
            min_confidence = 3
        else:
            min_confidence = 2

        if market.volatility > 0.02:
            min_confidence = min(5, min_confidence + 1)

        # Таймауты
        timeout_minutes = style_params['timeout_minutes']
        cycle_seconds = style_params['cycle_seconds']
        if market.volatility > 0.015:
            cycle_seconds = max(5, cycle_seconds - 2)

        # SHORT
        use_short = False
        short_score_threshold = -20
        if total_capital >= 50000:
            use_short = True
            short_score_threshold = -2
            success(f"🔻 SHORT АВТО-ВКЛЮЧЁН (капитал {total_capital:.0f}₽ >= 50000₽)")

        # Максимум позиций
        if total_capital < 20000:
            max_positions = 1
        elif total_capital < 50000:
            max_positions = 1
        elif total_capital < 100000:
            max_positions = 2
        else:
            max_positions = 3

        # Минимальная сумма сделки
        if total_capital < 20000:
            min_trade_amount = max(500, int(total_capital * 0.08))
        else:
            min_trade_amount = max(300, int(total_capital * 0.05))
        min_trade_amount = ((min_trade_amount + 9) // 10) * 10

        # Ценовые фильтры
        min_share_price, max_share_price = self._calculate_price_filters()
        if min_share_price is None:
            min_share_price = 5
            max_share_price = 2000

        # Вывод параметров
        success(f"\n📊 АВТОМАТИЧЕСКИЕ ПАРАМЕТРЫ:")
        success(f"   🎯 Стиль: {trading_style.value} ({style_reason})")
        success(f"   💰 Капитал: {total_capital:.0f}₽")
        success(f"   📈 Волатильность: {market.volatility * 100:.2f}%")
        success(f"   📊 Тренд: {market.trend_direction}")
        success(f"   🎯 Тейк: +{take_profit:.1f}%")
        success(f"   🛑 Стоп: -{stop_loss:.1f}%")
        success(f"   🔻 Трейлинг: {trailing_stop:.2f}%")
        success(f"   ⏰ Таймаут: {timeout_minutes} мин")
        success(f"   🔄 Цикл: {cycle_seconds} сек")
        success(f"   📊 Размер позиции: {position_size_pct * 100:.0f}%")
        success(f"   💰 Мин. сделка: {min_trade_amount}₽")
        success(f"   📈 Макс. позиций: {max_positions}")
        success(f"   💲 Цены: {min_share_price} - {max_share_price}₽")
        success(f"   🎫 Порог входа: score ≥ {min_confidence}")
        success(f"   🔻 SHORT: {'✅ ВКЛ' if use_short else '❌ ВЫКЛ'}")

        return {
            'trading_style': trading_style.value,
            # ✅ ИСПРАВЛЕНО: возвращаем ПРОЦЕНТЫ, а не множители
            'take_profit_pct': take_profit,  # например 1.5
            'stop_loss_pct': stop_loss,  # например 0.8
            'trailing_stop_pct': trailing_stop,  # например 0.5
            'timeout_minutes': timeout_minutes,
            'cycle_seconds': cycle_seconds,
            'position_size_pct': position_size_pct * 100,  # в процентах (например 6.0)
            'max_positions': max_positions,
            'min_trade_amount': min_trade_amount,
            'min_share_price': min_share_price,
            'max_share_price': max_share_price,
            'long_score_threshold': min_confidence,  # ← для LONG
            'short_score_threshold': short_score_threshold,  # ← для SHORT
            'use_short': use_short,
            'short_vwap_threshold': 1.02,
            'short_volume_spike': 2.0,
            'otc_timeout_multiplier': self.otc_timeout_multiplier,
            'max_hold_minutes': style_params['max_hold_minutes'],
        }

    def _calculate_price_filters(self) -> Tuple[int, int]:
        """Расчёт ценовых фильтров на основе рыночных данных"""
        try:
            all_shares = _get_tbank().get_all_shares(limit=500)
            prices = []
            for stock in all_shares:
                if stock.get('api_trade_available', False) and stock.get('currency') == 'rub':
                    price = _get_tbank().get_current_price(stock['figi'])
                    if price and 1 <= price <= 5000:
                        prices.append(price)

            if prices:
                prices.sort()
                min_price = prices[len(prices) // 20] if len(prices) > 20 else prices[0]
                max_price = prices[-len(prices) // 20] if len(prices) > 20 else prices[-1]
                min_share_price = max(5, int(min_price / 5) * 5)
                max_share_price = min(2000, int(max_price / 100) * 100 + 100)
                return min_share_price, max_share_price

        except Exception:
            pass

        return None, None


# Глобальный экземпляр
market_analyzer = MarketAnalyzer()