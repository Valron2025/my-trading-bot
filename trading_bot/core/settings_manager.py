"""Менеджер настроек бота - с сохранением в файл"""

import json
import os
import threading
from typing import Dict, Any, List
from pathlib import Path

from ..logger import info, success, warning, debug


class SettingsSubscriber:
    """Наблюдатель за изменениями настроек"""
    _subscribers: Dict[str, List[callable]] = {}

    @classmethod
    def subscribe(cls, key: str, callback: callable):
        if key not in cls._subscribers:
            cls._subscribers[key] = []
        cls._subscribers[key].append(callback)

    @classmethod
    def notify(cls, key: str, value: Any):
        if key in cls._subscribers:
            for callback in cls._subscribers[key]:
                try:
                    callback(key, value)
                except Exception as e:
                    debug(f"Ошибка в callback для {key}: {e}")


class SettingsManager:
    """Управление настройками бота с сохранением в JSON"""

    DEFAULT_SETTINGS = {
        # ========== ТОРГОВЫЕ ПАРАМЕТРЫ ==========
        'take_profit_pct': 1.5,
        'stop_loss_pct': 1.0,
        'trailing_stop_pct': 0.5,
        'max_positions': 5,
        'min_trade_amount': 500,
        'position_size_pct': 8.0,
        'score_threshold_long': 0,
        'score_threshold_short': -10,
        'timeout_minutes': 30,
        'cycle_seconds': 5,
        'short_enabled': True,
        'use_short': True,

        # ========== УПРАВЛЕНИЕ АНАЛИТИКОЙ ==========
        'fundamental_enabled': True,
        'technical_enabled': True,
        'news_enabled': True,
        'candle_analysis': True,
        'correlation_analysis': False,
        'use_fundamental_in_trading': True,
        'sentiment_impact_max': 5,

        # ========== УПРАВЛЕНИЕ СТРАТЕГИЕЙ ==========
        'use_scalping': True,
        'use_swing': True,
        'use_position': True,
        'use_margin': True,
        'aggressiveness': 7,

        # ========== УПРАВЛЕНИЕ РИСКАМИ ==========
        'risk_per_trade': 0.08,
        'max_daily_loss_pct': 5.0,
        'max_drawdown_pct': 15.0,
        'use_trailing_stop': True,
        'use_timeout': True,

        # ========== ФИЛЬТРАЦИЯ ==========
        'min_volume_ratio': 0.5,
        'max_spread_pct': 0.5,
        'min_liquidity_rank': 0,
        'blacklist_enabled': True,
        'auto_blacklist': True,

        # ========== МУЛЬТИТАЙМФРЕЙМНЫЙ АНАЛИЗ ==========
        'use_multi_timeframe': True,
        'mtf_boost': 3,
        'mtf_min_confidence': 50,
        'mtf_consensus_bonus': 2,

        # ========== НАСТРОЙКИ ТАЙМФРЕЙМОВ ==========
        'tf_1min_weight': 1.0,
        'tf_5min_weight': 1.5,
        'tf_15min_weight': 2.0,
        'tf_1hour_weight': 2.5,
    }

    def __init__(self, config_file: str = "bot_settings.json"):
        self.config_file = Path(config_file)
        self._settings = self.DEFAULT_SETTINGS.copy()
        self._lock = threading.RLock()
        self._load()

    def _load(self):
        """Загрузка настроек из файла"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for key, value in data.items():
                        if key in self._settings:
                            self._settings[key] = value
                info(f"⚙️ Настройки загружены из {self.config_file}")
            except Exception as e:
                warning(f"⚠️ Ошибка загрузки настроек: {e}")

        # ========== ПРИНУДИТЕЛЬНОЕ ВКЛЮЧЕНИЕ ==========
        # Аналитика
        self._settings['fundamental_enabled'] = True
        self._settings['use_fundamental_in_trading'] = True
        self._settings['technical_enabled'] = True
        self._settings['news_enabled'] = True
        self._settings['candle_analysis'] = True
        self._settings['correlation_analysis'] = True

        # Торговые режимы
        self._settings['short_enabled'] = True
        self._settings['use_short'] = True
        self._settings['use_position'] = True
        self._settings['use_margin'] = True
        self._settings['use_scalping'] = True
        self._settings['use_swing'] = True

        # Параметры риска
        self._settings['aggressiveness'] = 7
        self._settings['risk_per_trade'] = 0.08
        self._settings['max_positions'] = 5
        self._settings['min_trade_amount'] = 300

        # Score пороги
        self._settings['score_threshold_long'] = 2
        self._settings['score_threshold_short'] = -2

        # TP/SL
        self._settings['take_profit_pct'] = 2.0
        self._settings['stop_loss_pct'] = 0.8
        self._settings['trailing_stop_pct'] = 0.5
        self._settings['use_trailing_stop'] = True

        # Мультитаймфреймные настройки
        self._settings['use_multi_timeframe'] = True
        self._settings['mtf_boost'] = 3
        self._settings['mtf_min_confidence'] = 50
        self._settings['mtf_consensus_bonus'] = 2
        self._settings['tf_1min_weight'] = 1.0
        self._settings['tf_5min_weight'] = 1.5
        self._settings['tf_15min_weight'] = 2.0
        self._settings['tf_1hour_weight'] = 2.5

        info("🛡️ Принудительные настройки применены (максимальный режим)")
        info("   📊 Корреляционный анализ: ВКЛЮЧЁН")
        info("   📊 Мультитаймфреймный анализ: ВКЛЮЧЁН")

        # Применяем настройки к глобальному config (без аналитики)
        self._apply_to_config()

    def _save(self):
        """Сохранение настроек в файл"""
        try:
            with self._lock:
                with open(self.config_file, 'w', encoding='utf-8') as f:
                    json.dump(self._settings, f, indent=2, ensure_ascii=False)
            debug(f"💾 Настройки сохранены в {self.config_file}")
        except Exception as e:
            warning(f"⚠️ Ошибка сохранения настроек: {e}")

    def _apply_to_config(self):
        """Применение настроек к глобальному config (без аналитики)"""
        try:
            from ..config import config

            # Основные торговые параметры
            config.take_profit_pct = self._settings['take_profit_pct']
            config.stop_loss_pct = self._settings['stop_loss_pct']
            config.trailing_stop_pct = self._settings['trailing_stop_pct']
            config.max_positions = int(self._settings['max_positions'])
            config.min_trade_amount = self._settings['min_trade_amount']
            config.adaptive_position_size_pct = self._settings['position_size_pct'] / 100
            config.long_score_threshold = self._settings['score_threshold_long']
            config.short_score_threshold = self._settings['score_threshold_short']
            config.adaptive_timeout_minutes = int(self._settings['timeout_minutes'])
            config.adaptive_cycle_seconds = int(self._settings['cycle_seconds'])
            config.use_short = self._settings['short_enabled']

            # Новые настройки
            config.risk_per_trade = self._settings['risk_per_trade']
            config.max_news_impact = self._settings['sentiment_impact_max']

            # Настройки фильтрации
            if hasattr(config, 'exchange_min_volume_ratio'):
                config.exchange_min_volume_ratio = self._settings['min_volume_ratio']
            if hasattr(config, 'exchange_max_spread_pct'):
                config.exchange_max_spread_pct = self._settings['max_spread_pct']

            debug("⚙️ Настройки применены к config")

        except Exception as e:
            warning(f"⚠️ Ошибка применения настроек: {e}")

    def apply_analytics_settings(self, bot):
        """
        Применение настроек аналитики к компонентам бота.
        ВЫЗЫВАТЬ ПОСЛЕ ПОЛНОЙ ИНИЦИАЛИЗАЦИИ БОТА!
        """
        try:
            # Фундаментальный анализатор
            if hasattr(bot, 'fundamental_analyzer') and bot.fundamental_analyzer:
                bot.fundamental_analyzer.enabled = self._settings['fundamental_enabled']
                info(f"📊 FundamentalAnalyzer: {'ВКЛЮЧЁН' if self._settings['fundamental_enabled'] else 'ВЫКЛЮЧЁН'}")

            # Технический анализатор
            if hasattr(bot, 'technical_analyzer') and bot.technical_analyzer:
                bot.technical_analyzer.enabled = self._settings['technical_enabled']
                info(f"📈 TechnicalAnalyzer: {'ВКЛЮЧЁН' if self._settings['technical_enabled'] else 'ВЫКЛЮЧЁН'}")

            # Новостной анализатор
            if hasattr(bot, 'news_analyzer') and bot.news_analyzer:
                bot.news_analyzer.enabled = self._settings['news_enabled']
                bot.news_analyzer.max_impact = self._settings['sentiment_impact_max']
                info(f"📰 NewsAnalyzer: {'ВКЛЮЧЁН' if self._settings['news_enabled'] else 'ВЫКЛЮЧЁН'}")

            # Использование аналитики в торговле
            if hasattr(bot, 'use_fundamental_in_trading'):
                bot.use_fundamental_in_trading = self._settings['use_fundamental_in_trading']
                info(f"🎯 Использование аналитики в торговле: {'ДА' if self._settings['use_fundamental_in_trading'] else 'НЕТ'}")

            info("✅ Настройки аналитики применены")

        except Exception as e:
            debug(f"Ошибка применения аналитики: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        """Получение настройки"""
        return self._settings.get(key, default)

    def set(self, key: str, value: Any) -> bool:
        if key not in self._settings:
            return False

        with self._lock:
            # Синхронизация синонимов
            if key == 'short_enabled':
                self._settings['use_short'] = value
            elif key == 'use_short':
                self._settings['short_enabled'] = value

            # Специальная обработка для некоторых ключей
            if key == 'aggressiveness':
                self._apply_aggressiveness(value)
            elif key == 'short_enabled' and not value:
                self._close_all_shorts()

            self._settings[key] = value
            self._save()
            self._apply_to_config()

            SettingsSubscriber.notify(key, value)

            info(f"⚙️ Настройка {key} изменена: {value}")
            return True

    def _apply_aggressiveness(self, level: int):
        """Применение уровня агрессивности"""
        if level <= 3:
            self._settings['risk_per_trade'] = 0.03
            self._settings['max_positions'] = 2
            self._settings['score_threshold_long'] = 5
            self._settings['use_scalping'] = False
            self._settings['use_swing'] = True
        elif level <= 7:
            self._settings['risk_per_trade'] = 0.06
            self._settings['max_positions'] = 3
            self._settings['score_threshold_long'] = 3
            self._settings['use_scalping'] = True
            self._settings['use_swing'] = True
        else:
            self._settings['risk_per_trade'] = 0.10
            self._settings['max_positions'] = 5
            self._settings['score_threshold_long'] = 1
            self._settings['use_scalping'] = True
            self._settings['use_swing'] = True
            self._settings['use_position'] = True

    def _close_all_shorts(self):
        """Закрытие всех SHORT позиций при отключении"""
        try:
            from trading_bot.api.tbank_client import tbank
            positions = tbank.get_positions()
            for pos in positions:
                if pos.get('quantity', 0) < 0:
                    ticker = pos.get('ticker', 'unknown')
                    info(f"🔒 Закрытие SHORT {ticker} при отключении SHORT торговли")
                    tbank.buy(pos['figi'], abs(pos['quantity']))
        except Exception as e:
            warning(f"Ошибка закрытия SHORT: {e}")

    def get_all(self) -> Dict[str, Any]:
        """Получение всех настроек"""
        return self._settings.copy()

    def get_settings_text(self) -> str:
        """Форматированный текст настроек для Telegram"""
        text = f"""⚙️ <b>НАСТРОЙКИ БОТА</b>

    ━━━━━━━━━━━━━━━━━━━━━━━
    📊 <b>ТОРГОВЫЕ ПАРАМЕТРЫ</b>
    ━━━━━━━━━━━━━━━━━━━━━━━
    🎯 Тейк-профит: <b>+{self._settings['take_profit_pct']:.1f}%</b>
    🛑 Стоп-лосс: <b>-{self._settings['stop_loss_pct']:.1f}%</b>
    🔻 Трейлинг-стоп: <b>{self._settings['trailing_stop_pct']:.2f}%</b>
    📊 Макс. позиций: <b>{self._settings['max_positions']}</b>
    💰 Мин. сумма сделки: <b>{self._settings['min_trade_amount']}₽</b>
    📈 Размер позиции: <b>{self._settings['position_size_pct']:.0f}%</b>
    🎫 Score порог LONG: <b>≥ {self._settings['score_threshold_long']}</b>
    🎫 Score порог SHORT: <b>≤ {self._settings['score_threshold_short']}</b>
    ⏰ Таймаут позиции: <b>{self._settings['timeout_minutes']} мин</b>
    🔄 Интервал цикла: <b>{self._settings['cycle_seconds']} сек</b>
    🔻 SHORT торговля: <b>{'✅ ВКЛ' if self._settings['short_enabled'] else '❌ ВЫКЛ'}</b>
    💰 Риск на сделку: <b>{self._settings['risk_per_trade'] * 100:.0f}%</b>

    ━━━━━━━━━━━━━━━━━━━━━━━
    🔬 <b>АНАЛИТИКА</b>
    ━━━━━━━━━━━━━━━━━━━━━━━
    📊 Фундаментальный: <b>{'✅' if self._settings['fundamental_enabled'] else '❌'}</b>
    📈 Технический: <b>{'✅' if self._settings['technical_enabled'] else '❌'}</b>
    📰 Новостной: <b>{'✅' if self._settings['news_enabled'] else '❌'}</b>
    🕯️ Свечной: <b>{'✅' if self._settings['candle_analysis'] else '❌'}</b>
    🔄 Корреляционный: <b>{'✅' if self._settings['correlation_analysis'] else '❌'}</b>
    📊 Мультитаймфрейм: <b>{'✅' if self._settings.get('use_multi_timeframe', True) else '❌'}</b>
    🎯 Использовать в торговле: <b>{'✅' if self._settings['use_fundamental_in_trading'] else '❌'}</b>

    ━━━━━━━━━━━━━━━━━━━━━━━
    🧠 <b>СТРАТЕГИЯ</b>
    ━━━━━━━━━━━━━━━━━━━━━━━
    ⚡ Скальпинг: <b>{'✅' if self._settings['use_scalping'] else '❌'}</b>
    🔄 Свинг: <b>{'✅' if self._settings['use_swing'] else '❌'}</b>
    📦 Позиционная: <b>{'✅' if self._settings['use_position'] else '❌'}</b>
    💳 Маржа: <b>{'✅' if self._settings['use_margin'] else '❌'}</b>
    📊 Агрессивность: <b>{self._settings['aggressiveness']}/10</b>
    """
        return text

    def get_analytics_text(self) -> str:
        """Текст настроек аналитики"""
        text = f"""🔬 <b>НАСТРОЙКИ АНАЛИТИКИ</b>

📊 Фундаментальный анализ: <b>{'✅ ВКЛЮЧЁН' if self._settings['fundamental_enabled'] else '❌ ВЫКЛЮЧЁН'}</b>
   P/E, ROE, дивиденды, мультипликаторы

📈 Технический анализ: <b>{'✅ ВКЛЮЧЁН' if self._settings['technical_enabled'] else '❌ ВЫКЛЮЧЁН'}</b>
   RSI, MACD, объёмы, уровни

📰 Новостной анализ: <b>{'✅ ВКЛЮЧЁН' if self._settings['news_enabled'] else '❌ ВЫКЛЮЧЁН'}</b>
   Сентимент новостей, влияние ±{self._settings['sentiment_impact_max']}

🕯️ Свечной анализ: <b>{'✅ ВКЛЮЧЁН' if self._settings['candle_analysis'] else '❌ ВЫКЛЮЧЁН'}</b>
   Паттерны, формации свечей

🔄 Корреляционный анализ: <b>{'✅ ВКЛЮЧЁН' if self._settings['correlation_analysis'] else '❌ ВЫКЛЮЧЁН'}</b>
   Связи между инструментами

━━━━━━━━━━━━━━━━━━━━━━━
🎯 <b>ИСПОЛЬЗОВАНИЕ В ТОРГОВЛЕ</b>
━━━━━━━━━━━━━━━━━━━━━━━
Аналитика влияет на решения: <b>{'✅ ДА' if self._settings['use_fundamental_in_trading'] else '❌ НЕТ'}</b>
"""
        return text

    def get_strategy_text(self) -> str:
        """Текст настроек стратегии"""
        text = f"""🧠 <b>НАСТРОЙКИ СТРАТЕГИИ</b>

━━━━━━━━━━━━━━━━━━━━━━━
📊 <b>РЕЖИМЫ ТОРГОВЛИ</b>
━━━━━━━━━━━━━━━━━━━━━━━
⚡ Скальпинг: <b>{'✅ ВКЛЮЧЁН' if self._settings['use_scalping'] else '❌ ВЫКЛЮЧЁН'}</b>
   Короткие сделки (минуты-часы)

🔄 Свинг-трейдинг: <b>{'✅ ВКЛЮЧЁН' if self._settings['use_swing'] else '❌ ВЫКЛЮЧЁН'}</b>
   Среднесрочные сделки (часы-дни)

📦 Позиционная: <b>{'✅ ВКЛЮЧЁН' if self._settings['use_position'] else '❌ ВЫКЛЮЧЁН'}</b>
   Долгосрочные сделки (дни-недели)

━━━━━━━━━━━━━━━━━━━━━━━
📈 <b>АГРЕССИВНОСТЬ</b>
━━━━━━━━━━━━━━━━━━━━━━━
Уровень: <b>{self._settings['aggressiveness']}/10</b>
{self._get_aggressiveness_bar()}

Риск на сделку: <b>{self._settings['risk_per_trade']*100:.0f}%</b>
Макс. позиций: <b>{self._settings['max_positions']}</b>
Score порог: <b>≥ {self._settings['score_threshold_long']}</b>

━━━━━━━━━━━━━━━━━━━━━━━
💳 <b>ДОПОЛНИТЕЛЬНО</b>
━━━━━━━━━━━━━━━━━━━━━━━
Маржинальная торговля: <b>{'✅ ВКЛЮЧЕНА' if self._settings['use_margin'] else '❌ ВЫКЛЮЧЕНА'}</b>
"""
        return text

    def _get_aggressiveness_bar(self) -> str:
        """Получение визуальной шкалы агрессивности"""
        level = self._settings['aggressiveness']
        bar = "🔴" * level + "⚪" * (10 - level)
        return f"   {bar}\n   (1=консервативный, 10=агрессивный)"

    def reset_to_defaults(self):
        """Сброс настроек до значений по умолчанию"""
        with self._lock:
            self._settings = self.DEFAULT_SETTINGS.copy()
            self._save()
            self._apply_to_config()
        success("⚙️ Настройки сброшены до значений по умолчанию")

    # ========== ДОПОЛНИТЕЛЬНЫЕ МЕТОДЫ ==========

    def update_component_settings(self, bot):
        """Обновление настроек компонентов бота"""
        try:
            if hasattr(bot, 'fundamental_analyzer') and bot.fundamental_analyzer:
                bot.fundamental_analyzer.enabled = self._settings['fundamental_enabled']

            if hasattr(bot, 'technical_analyzer') and bot.technical_analyzer:
                bot.technical_analyzer.enabled = self._settings['technical_enabled']

            if hasattr(bot, 'news_analyzer') and bot.news_analyzer:
                bot.news_analyzer.enabled = self._settings['news_enabled']
                bot.news_analyzer.max_impact = self._settings['sentiment_impact_max']

            if hasattr(bot, 'use_fundamental_in_trading'):
                bot.use_fundamental_in_trading = self._settings['use_fundamental_in_trading']

            info("✅ Настройки компонентов бота обновлены")

        except Exception as e:
            warning(f"⚠️ Ошибка обновления компонентов: {e}")

    def get_aggressiveness_level(self) -> int:
        return self._settings.get('aggressiveness', 5)

    def is_short_allowed(self) -> bool:
        return self._settings.get('short_enabled', False) and self._settings.get('use_short', False)

    def is_margin_allowed(self) -> bool:
        return self._settings.get('use_margin', False)

    def get_risk_per_trade(self) -> float:
        return self._settings.get('risk_per_trade', 0.06)

    def get_max_positions(self) -> int:
        return int(self._settings.get('max_positions', 5))

    def get_score_threshold_long(self) -> int:
        return int(self._settings.get('score_threshold_long', 2))

    def get_score_threshold_short(self) -> int:
        return int(self._settings.get('score_threshold_short', -2))

    def is_multi_timeframe_enabled(self) -> bool:
        return self._settings.get('use_multi_timeframe', True)

    def get_mtf_boost(self) -> int:
        return int(self._settings.get('mtf_boost', 3))

    def get_mtf_min_confidence(self) -> int:
        return int(self._settings.get('mtf_min_confidence', 50))

    def get_mtf_consensus_bonus(self) -> int:
        return int(self._settings.get('mtf_consensus_bonus', 2))

    def get_tf_weights(self) -> Dict[str, float]:
        return {
            '1min': self._settings.get('tf_1min_weight', 1.0),
            '5min': self._settings.get('tf_5min_weight', 1.5),
            '15min': self._settings.get('tf_15min_weight', 2.0),
            '1hour': self._settings.get('tf_1hour_weight', 2.5),
        }

    def set_tf_weight(self, tf: str, weight: float) -> bool:
        key = f'tf_{tf}_weight'
        if key in self._settings:
            self._settings[key] = weight
            self._save()
            info(f"⚙️ Вес таймфрейма {tf} изменён: {weight}")
            return True
        return False


# Глобальный экземпляр
settings_manager = SettingsManager()