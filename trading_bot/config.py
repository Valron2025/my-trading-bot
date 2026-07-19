"""Модуль конфигурации - ПРОДАКШЕН ВЕРСИЯ 24/7 (полностью автоматический)"""

import os
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Tuple
from dotenv import load_dotenv

# ✅ НЕТ ИМПОРТОВ ЛОГГЕРА - вообще убираем

load_dotenv()


@dataclass
class TradingConfig:
    """Конфигурация - продакшен версия (ВСЕ ПАРАМЕТРЫ АВТОМАТИЧЕСКИ)"""

    # ========== API КЛЮЧИ ==========
    tbank_token: str = field(default_factory=lambda: os.getenv("TBANK_TOKEN", ""))
    tbank_account_id: Optional[str] = field(default_factory=lambda: os.getenv("TBANK_ACCOUNT_ID"))

    tbank_api_url: str = field(default_factory=lambda: os.getenv(
        "TBANK_API_URL", 
        "invest-public-api.tbank.ru:443"
    ))
    
    telegram_token: Optional[str] = field(default_factory=lambda: os.getenv("TELEGRAM_TOKEN"))
    telegram_chat_id: Optional[str] = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID"))

    # ========== РЕЖИМ СИМУЛЯЦИИ ==========
    simulation_mode: bool = field(default_factory=lambda: os.getenv("SIMULATION_MODE", "false").lower() == "true")

    # ========== ТОРГОВЫЕ ПАРАМЕТРЫ ==========
    trading_style: str = "AUTO"
    use_short: bool = False
    use_limit_orders: bool = False
    use_market_orders: bool = False
    prefer_market_in_main: bool = True
    use_dynamic_sltp: bool = True

    take_profit_pct: float = 1.5
    stop_loss_pct: float = 1.0
    trailing_stop_pct: float = 0.5
    max_positions: int = 3
    min_trade_amount: int = 300
    min_share_price: float = 5
    max_share_price: float = 2000

    # ========== НАСТРОЙКИ ДЛЯ БЫСТРОГО РЕАГИРОВАНИЯ ==========
    position_check_interval_seconds: int = 2  # проверка позиций каждые 2 секунды
    max_scan_tickers: int = 50  # максимум тикеров для сканирования
    emergency_mode: bool = False  # экстренный режим (сканировать только позиции)

    # ========== АДАПТИВНЫЕ ПАРАМЕТРЫ ==========
    adaptive_position_size_pct: float = 0.04
    adaptive_timeout_minutes: int = 30
    adaptive_cycle_seconds: int = 30

    # ========== АДАПТИВНЫЕ ПАРАМЕТРЫ ДЛЯ РАЗНЫХ РЕЖИМОВ ==========
    exchange_min_avg_volume: int = 50000
    exchange_min_volume_ratio: float = 0.5
    exchange_min_trade_amount: int = 500

    otc_min_avg_volume: int = 5000
    otc_min_volume_ratio: float = 0.3
    otc_min_trade_amount: int = 200

    is_otc_mode: bool = False

    # ========== ПОРОГИ ==========
    long_score_threshold: float = 0
    short_score_threshold: float = -10

    # ========== ТЕХНИЧЕСКИЕ ПАРАМЕТРЫ ==========
    price_cache_ttl: int = 5
    temp_blacklist_duration_minutes: int = 10
    micro_capital_threshold: int = 5000
    micro_capital_threshold_min: int = 500
    micro_capital_warning: int = 2000
    min_capital_for_trading: int = 500
    min_capital_for_short: int = 7000
    capital_reserve_ratio = 0.2

    # ========== SHORT ПАРАМЕТРЫ ==========
    short_vwap_threshold_base: float = 1.02
    short_volume_spike_base: float = 2.0
    short_vwap_threshold: float = 1.02
    short_volume_spike: float = 2.0

    # ========== ПАРАМЕТРЫ МАРЖИ ==========
    margin_usage_ratio: float = 0.3
    min_reserve_after_trade: float = 200
    max_position_pct_of_own: float = 1.5
    max_position_pct_of_effective: float = 0.5
    close_reserve_ratio: float = 0.3

    # ========== ЛОГИРОВАНИЕ ==========
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    debug_mode: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    force_trading_enabled: bool = field(default_factory=lambda: os.getenv("FORCE_TRADING_ENABLED", "false").lower() == "true")

    # ========== ЗАЩИТА ==========
    max_fee_pct_of_capital: float = 2.0
    emergency_close_on_high_fee: bool = True

    # ========== OTC ==========
    otc_enabled: bool = field(default_factory=lambda: os.getenv("ENABLE_OTC_TRADING", "false").lower() == "true")
    otc_only: bool = field(default_factory=lambda: os.getenv("OTC_ONLY", "false").lower() == "true")
    otc_max_price_deviation: float = float(os.getenv("OTC_MAX_PRICE_DEVIATION", "0.03"))

    # ========== ВЕЧЕРНЯЯ СЕССИЯ ==========
    evening_session_check_enabled: bool = True

    # ========== ВНУТРЕННИЕ ==========
    total_capital: float = 0
    market_volatility: float = 0.01
    market_trend: float = 0

    # ========== СТИЛИ ТОРГОВЛИ ==========
    trading_styles: dict = field(default_factory=lambda: {
        "micro": {"long_threshold": 3, "position_size": 0.05, "max_positions": 2, "stop_loss": 0.7,
                  "take_profit": 1.2, "timeout": 15, "cycle": 8},
        "small": {"long_threshold": 4, "position_size": 0.07, "max_positions": 3, "stop_loss": 0.9,
                  "take_profit": 1.4, "timeout": 20, "cycle": 6},
        "medium": {"long_threshold": 5, "position_size": 0.09, "max_positions": 4, "stop_loss": 1.0,
                   "take_profit": 1.5, "timeout": 30, "cycle": 5},
        "large": {"long_threshold": 6, "position_size": 0.12, "max_positions": 5, "stop_loss": 1.2,
                  "take_profit": 1.8, "timeout": 45, "cycle": 4},
    })

    # ========== РЕЖИМЫ КАПИТАЛА ==========
    CAPITAL_MODES: dict = field(default_factory=lambda: {
        'critical': {'min': 0, 'max': 500, 'can_trade': False, 'position_pct': 0.0, 'take_profit_pct': 0.0,
                     'stop_loss_pct': 0.0, 'max_positions': 0, 'use_short': False, 'min_confidence': 10,
                     'min_trade_amount': 100, 'cycle_seconds': 300, 'short_allowed': False,
                     'message': '❌ КРИТИЧЕСКИ МАЛО СРЕДСТВ!'},

        'micro_grow': {'min': 500, 'max': 3000, 'can_trade': True, 'position_pct': 0.10, 'take_profit_pct': 3.0,
                       'stop_loss_pct': 1.2, 'max_positions': 1, 'use_short': False, 'min_confidence': 4,
                       'min_trade_amount': 150, 'cycle_seconds': 60, 'short_allowed': False,
                       'message': '🟡 РЕЖИМ НАКОПЛЕНИЯ'},

        'small_grow': {'min': 3000, 'max': 7000, 'can_trade': True, 'position_pct': 0.08, 'take_profit_pct': 2.5,
                       'stop_loss_pct': 1.0, 'max_positions': 4, 'use_short': False, 'min_confidence': 3,
                       'min_trade_amount': 200, 'cycle_seconds': 45, 'short_allowed': False,
                       'message': '🟢 МАЛЫЙ КАПИТАЛ'},

        'normal': {'min': 7000, 'max': 15000, 'can_trade': True, 'position_pct': 0.07, 'take_profit_pct': 2.0,
                   'stop_loss_pct': 0.8, 'max_positions': 6, 'use_short': True, 'min_confidence': 2,
                   'min_trade_amount': 300, 'cycle_seconds': 30, 'short_allowed': True,
                   'message': '🟢 НОРМАЛЬНЫЙ РЕЖИМ'},

        'full': {'min': 15000, 'max': float('inf'), 'can_trade': True, 'position_pct': 0.06, 'take_profit_pct': 1.5,
                 'stop_loss_pct': 0.6, 'max_positions': 10, 'use_short': True, 'min_confidence': 2,
                 'min_trade_amount': 500, 'cycle_seconds': 15, 'short_allowed': True,
                 'message': '🔵 ПОЛНЫЙ РЕЖИМ'}
    })

    # Торговые лимиты
    max_daily_trades: int = 50
    max_position_duration_minutes: int = 60

    # Риск-менеджмент
    max_correlation_threshold: float = 0.7
    max_portfolio_risk_pct: float = 0.05

    # Бэктестинг
    backtest_mode: bool = False

    def __post_init__(self):
        """Валидация и загрузка капитала"""
        self.min_trade_amount = int(max(100, min(5000, self.min_trade_amount)))
        self.adaptive_position_size_pct = max(0.02, min(0.20, self.adaptive_position_size_pct))

        # ⚠️ БЕЗ ЛОГИРОВАНИЯ - просто устанавливаем режимы
        self._load_total_capital_from_api()
        self._load_total_capital_from_settings()

    def is_evening_trading_allowed(self, ticker: str = None) -> bool:
        """Проверка, разрешена ли вечерняя торговля для тикера"""
        if not self.evening_session_check_enabled:
            return True

        if ticker is None:
            return True

        try:
            from trading_bot.api.tbank_client import tbank
            figi = tbank._get_figi_by_ticker(ticker)
            if figi:
                liquidity = tbank.check_liquidity(figi, required_volume=5000, min_depth=3)
                if liquidity.get('is_liquid', False):
                    return True

                status = tbank.get_trading_status(figi)
                if status.get('api_trade_available', False) and status.get('limit_order_available', False):
                    return True
            return False
        except Exception:
            return True

    def _load_total_capital_from_settings(self):
        """Загрузка total_capital из bot_settings.json"""
        try:
            settings_file = Path("bot_settings.json")
            if settings_file.exists():
                with open(settings_file, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                    if 'total_capital' in settings:
                        self.total_capital = settings['total_capital']
                    else:
                        pass  # Не логируем
            else:
                pass  # Не логируем
        except Exception:
            pass  # Не логируем

    def _load_total_capital_from_api(self):
        """Загрузка реального капитала из API"""
        try:
            from trading_bot.api.tbank_client import tbank
            # ✅ Проверяем, что клиент доступен
            if tbank is None:  # ← ЭТА СТРОКА ДОЛЖНА БЫТЬ
                return
            _, total, _ = tbank.get_available_funds()
            if total > 0:
                self.total_capital = total
        except Exception:
            pass  # API может быть недоступен при первом запуске

    # ========== АВТОМАТИЧЕСКИЕ МЕТОДЫ ==========

    def get_capital_mode(self, capital: float) -> dict:
        for mode_name, mode_config in self.CAPITAL_MODES.items():
            if mode_config['min'] <= capital < mode_config['max']:
                return mode_config
        return self.CAPITAL_MODES['full']

    def get_trading_style_name(self, capital: float) -> str:
        if capital < 3000:
            return "micro"
        elif capital < 10000:
            return "small"
        elif capital < 50000:
            return "medium"
        else:
            return "large"

    def get_adaptive_position_size(self, capital: float) -> float:
        mode = self.get_capital_mode(capital)
        return mode['position_pct']

    def get_adaptive_max_positions(self, capital: float) -> int:
        mode = self.get_capital_mode(capital)
        return mode['max_positions']

    def get_adaptive_stop_loss(self, capital: float) -> float:
        mode = self.get_capital_mode(capital)
        base_sl = mode['stop_loss_pct']

        if self.market_volatility > 0.02:
            return base_sl * 1.3
        elif self.market_volatility < 0.005:
            return base_sl * 0.7
        return base_sl

    def get_adaptive_take_profit(self, capital: float) -> float:
        mode = self.get_capital_mode(capital)
        base_tp = mode['take_profit_pct']

        if self.market_volatility > 0.02:
            return base_tp * 1.2
        elif self.market_volatility < 0.005:
            return base_tp * 0.8
        return base_tp

    def get_adaptive_trailing(self, capital: float) -> float:
        mode = self.get_capital_mode(capital)
        return mode.get('trailing_pct', 0.4)

    def get_adaptive_timeout(self, capital: float) -> int:
        mode = self.get_capital_mode(capital)
        # ✅ Исправляем: timeout вместо timeout_minutes
        return mode.get('timeout', 30)  # было 'timeout_minutes'

    def get_adaptive_cycle(self, capital: float) -> int:
        mode = self.get_capital_mode(capital)
        return mode.get('cycle_seconds', 30)

    def get_adaptive_long_threshold(self, capital: float) -> float:
        mode = self.get_capital_mode(capital)
        if not mode['can_trade']:
            return 10
        base_threshold = mode['min_confidence']

        if self.market_volatility > 0.02:
            return min(10, base_threshold + 1)
        return base_threshold

    def get_adaptive_short_threshold(self, capital: float) -> float:
        mode = self.get_capital_mode(capital)

        if not mode['can_trade'] or not mode.get('short_allowed', False):
            return -20

        if capital < self.min_capital_for_short:
            return -20

        base_threshold = -mode['min_confidence']

        if self.market_volatility > 0.02:
            return base_threshold - 1
        return base_threshold

    def get_adaptive_min_trade_amount(self, capital: float) -> int:
        mode = self.get_capital_mode(capital)
        base_amount = mode.get('min_trade_amount', self.min_trade_amount)

        # ✅ Убеждаемся, что base_amount - int
        if isinstance(base_amount, float):
            base_amount = int(base_amount)

        if self.market_volatility > 0.02:
            return max(100, int(base_amount * 0.8))  # минимум 100₽
        return max(100, base_amount)

    def get_adaptive_vwap_threshold(self, capital: float) -> float:
        mode = self.get_capital_mode(capital)
        if not mode.get('short_allowed', False) or capital < self.min_capital_for_short:
            return 999

        if capital < 10000:
            return 1.01
        elif capital < 30000:
            return 1.02
        else:
            return 1.03

    def get_adaptive_volume_spike(self, capital: float) -> float:
        mode = self.get_capital_mode(capital)
        if not mode.get('short_allowed', False) or capital < self.min_capital_for_short:
            return 999

        if capital < 10000:
            return 1.8
        elif capital < 30000:
            return 2.0
        else:
            return 2.2

    def is_trading_allowed_by_capital(self, capital: float) -> Tuple[bool, str]:
        mode = self.get_capital_mode(capital)
        if not mode['can_trade']:
            return False, mode['message']
        if capital < self.micro_capital_warning:
            return True, f"⚠️ микро-капитал {capital:.0f}₽"
        return True, "OK"

    def can_use_margin(self, total_capital: float) -> bool:
        return total_capital >= 5000

    def update_short_settings(self, total_capital: float) -> Tuple[bool, str]:
        mode = self.get_capital_mode(total_capital)

        short_allowed_by_mode = mode.get('short_allowed', False)
        short_allowed_by_capital = total_capital >= self.min_capital_for_short

        new_short_state = short_allowed_by_mode and short_allowed_by_capital

        if new_short_state:
            self.short_vwap_threshold = self.get_adaptive_vwap_threshold(total_capital)
            self.short_volume_spike = self.get_adaptive_volume_spike(total_capital)
            self.short_score_threshold = self.get_adaptive_short_threshold(total_capital)

        if new_short_state and not self.use_short:
            self.use_short = True
            msg = f"✅🔻 SHORT АВТОМАТИЧЕСКИ ВКЛЮЧЁН! Капитал: {total_capital:.0f}₽ | " \
                  f"Порог: score ≤ {self.short_score_threshold} | " \
                  f"VWAP: {self.short_vwap_threshold:.2f} | " \
                  f"Объём: {self.short_volume_spike:.1f}x"
            return True, msg

        elif not new_short_state and self.use_short:
            self.use_short = False
            self.short_score_threshold = -20
            self.short_vwap_threshold = 999
            self.short_volume_spike = 999
            msg = f"❌🔻 SHORT АВТОМАТИЧЕСКИ ОТКЛЮЧЁН! Капитал: {total_capital:.0f}₽"
            if not short_allowed_by_mode:
                msg += f" | Режим: {mode.get('message', 'unknown')}"
            if not short_allowed_by_capital:
                msg += f" | Нужно ≥ {self.min_capital_for_short}₽"
            return False, msg

        elif new_short_state and self.use_short:
            self.short_vwap_threshold = self.get_adaptive_vwap_threshold(total_capital)
            self.short_volume_spike = self.get_adaptive_volume_spike(total_capital)
            self.short_score_threshold = self.get_adaptive_short_threshold(total_capital)

        return self.use_short, "OK"

    def emergency_disable_short(self, margin_rate: float, consecutive_losses: int = 0) -> Tuple[bool, str]:
        if not self.use_short:
            return False, ""

        if margin_rate > 85:
            return True, f"КРИТИЧЕСКАЯ МАРЖА {margin_rate:.1f}% > 85%"

        if margin_rate > 70:
            return True, f"высокая маржа {margin_rate:.1f}% > 70%"

        if consecutive_losses >= 5:
            return True, f"серия убытков {consecutive_losses} подряд"

        return False, ""

    def update_market_conditions(self, volatility: float, trend: float):
        self.market_volatility = volatility
        self.market_trend = trend

    max_imbalance_ratio = 0.7
    max_total_capital_usage = 85
    max_single_position_pct = 60

    position_limits_by_capital = {
        0: 2,
        5000: 3,
        10000: 4,
        20000: 6,
        50000: 8,
        100000: 12,
    }

    # ========== НАСТРОЙКИ АЙСБЕРГ-ЗАЯВОК ==========
    large_position_threshold: int = 100  # Позиции больше 100 лотов используют айсберг
    iceberg_visible_ratio: float = 0.1  # Видимая часть = 10% от общего объёма
    iceberg_min_part_size: int = 10  # Минимальный размер одной части (лотов)
    iceberg_max_parts: int = 50  # Максимум частей для разбиения

    # ========== ПАРАЛЛЕЛЬНОЕ СКАНИРОВАНИЕ ==========
    use_parallel_scan: bool = True  # Включить параллельный режим
    max_concurrent_scans: int = 10  # Максимум параллельных анализов
    parallel_scan_limit: int = 30  # Максимум тикеров при параллельном сканировании


# Глобальный экземпляр
config = TradingConfig()

# ========== АЛИАСЫ ДЛЯ СОВМЕСТИМОСТИ ==========
# Некоторые модули ожидают класс Config
Config = TradingConfig

# Также экспортируем TradingConfig для явного импорта
__all__ = ['TradingConfig', 'Config', 'config']
