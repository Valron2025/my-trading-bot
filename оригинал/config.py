"""Модуль конфигурации - ПРОДАКШЕН ВЕРСИЯ 24/7 (полностью автоматический)"""

import os
from dataclasses import dataclass, field
from typing import Optional, Tuple
from dotenv import load_dotenv

from .logger import warning, info, debug

load_dotenv()


@dataclass
class TradingConfig:
    """Конфигурация - продакшен версия (ВСЕ ПАРАМЕТРЫ АВТОМАТИЧЕСКИ)"""

    # ========== API КЛЮЧИ (только это из .env) ==========
    tbank_token: str = field(default_factory=lambda: os.getenv("TBANK_TOKEN", ""))
    tbank_account_id: Optional[str] = field(default_factory=lambda: os.getenv("TBANK_ACCOUNT_ID"))
    telegram_token: Optional[str] = field(default_factory=lambda: os.getenv("TELEGRAM_TOKEN"))
    telegram_chat_id: Optional[str] = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID"))

    # ========== РЕЖИМ СИМУЛЯЦИИ ==========
    simulation_mode: bool = field(default_factory=lambda: os.getenv("SIMULATION_MODE", "false").lower() == "true")

    # ========== ТОРГОВЫЕ ПАРАМЕТРЫ (АВТОМАТИЧЕСКИ) ==========
    trading_style: str = "AUTO"  # Автоматический выбор стиля
    use_short: bool = False  # Автоматически включается при достаточном капитале
    use_limit_orders: bool = False
    use_dynamic_sltp: bool = True

    # Базовые значения (адаптируются под капитал)
    take_profit_pct: float = 1.5
    stop_loss_pct: float = 1.0
    trailing_stop_pct: float = 0.5
    max_positions: int = 5
    min_trade_amount: int = 500
    min_share_price: float = 5
    max_share_price: float = 2000

    # ========== АДАПТИВНЫЕ ПАРАМЕТРЫ ==========
    adaptive_position_size_pct: float = 0.08
    adaptive_timeout_minutes: int = 30
    adaptive_cycle_seconds: int = 5

    # ========== АДАПТИВНЫЕ ПАРАМЕТРЫ ДЛЯ РАЗНЫХ РЕЖИМОВ ==========
    # Биржевой режим (будни, нормальная ликвидность)
    exchange_min_avg_volume: int = 50000
    exchange_min_volume_ratio: float = 0.5
    exchange_min_trade_amount: int = 500

    # OTC режим (выходные, праздники, пониженная ликвидность)
    otc_min_avg_volume: int = 5000
    otc_min_volume_ratio: float = 0.3
    otc_min_trade_amount: int = 200

    # Текущий активный режим (автоматически определяется)
    is_otc_mode: bool = False

    # ========== ПОРОГИ (АВТОМАТИЧЕСКИ) ==========
    long_score_threshold: float = 0
    short_score_threshold: float = -10

    # ========== ТЕХНИЧЕСКИЕ ПАРАМЕТРЫ ==========
    price_cache_ttl: int = 5
    temp_blacklist_duration_minutes: int = 10
    micro_capital_threshold: int = 5000
    micro_capital_threshold_min: int = 500
    micro_capital_warning: int = 2000
    min_capital_for_trading: int = 500
    min_capital_for_short: int = 3000

    # ========== SHORT ПАРАМЕТРЫ (ВСЕ АВТОМАТИЧЕСКИ) ==========
    # Пороги определяются динамически на основе капитала и волатильности
    short_vwap_threshold_base: float = 1.02
    short_volume_spike_base: float = 2.0

    # Множители для SHORT (адаптируются)
    short_vwap_threshold: float = 1.02  # Актуальное значение
    short_volume_spike: float = 2.0     # Актуальное значение

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

    # ========== OTC (ВНЕБИРЖЕВАЯ ТОРГОВЛЯ) ==========
    otc_enabled: bool = field(default_factory=lambda: os.getenv("ENABLE_OTC_TRADING", "false").lower() == "true")
    otc_only: bool = field(default_factory=lambda: os.getenv("OTC_ONLY", "false").lower() == "true")
    otc_max_price_deviation: float = float(os.getenv("OTC_MAX_PRICE_DEVIATION", "0.03"))

    # ========== ВНУТРЕННИЕ ==========
    total_capital: float = 0
    market_volatility: float = 0.01  # Текущая волатильность рынка
    market_trend: float = 0  # Текущий тренд (-1 до 1)

    # ========== СТИЛИ ТОРГОВЛИ (АДАПТИВНЫЕ) ==========
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

    # ========== РЕЖИМЫ КАПИТАЛА (АВТОМАТИЧЕСКИ) ==========
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
                       'stop_loss_pct': 1.0, 'max_positions': 2, 'use_short': False, 'min_confidence': 3,
                       'min_trade_amount': 200, 'cycle_seconds': 45, 'short_allowed': False,
                       'message': '🟢 МАЛЫЙ КАПИТАЛ'},

        'normal': {'min': 7000, 'max': 15000, 'can_trade': True, 'position_pct': 0.07, 'take_profit_pct': 2.0,
                   'stop_loss_pct': 0.8, 'max_positions': 2, 'use_short': True, 'min_confidence': 2,
                   'min_trade_amount': 300, 'cycle_seconds': 30, 'short_allowed': True,
                   'message': '🟢 НОРМАЛЬНЫЙ РЕЖИМ'},

        'full': {'min': 15000, 'max': float('inf'), 'can_trade': True, 'position_pct': 0.06, 'take_profit_pct': 1.5,
                 'stop_loss_pct': 0.6, 'max_positions': 3, 'use_short': True, 'min_confidence': 2,
                 'min_trade_amount': 500, 'cycle_seconds': 15, 'short_allowed': True,
                 'message': '🔵 ПОЛНЫЙ РЕЖИМ'}
    })

    def __post_init__(self):
        """Валидация"""
        self.min_trade_amount = int(max(100, min(5000, self.min_trade_amount)))
        self.adaptive_position_size_pct = max(0.02, min(0.20, self.adaptive_position_size_pct))

        # OTC настройки
        if self.otc_only:
            self.use_short = False
            info("🌙 OTC РЕЖИМ: только внебиржевая торговля")
        elif self.otc_enabled:
            info("🌙 OTC РЕЖИМ: внебиржевая торговля разрешена в выходные")
        else:
            info("🏛️ БИРЖЕВОЙ РЕЖИМ: только торги на MOEX")

        if self.simulation_mode:
            self.use_short = False
            self.adaptive_position_size_pct = 0.0
            self.max_positions = 0
            info("🔧 РЕЖИМ СИМУЛЯЦИИ: реальные ордера отключены")
        else:
            info("🚀 РЕЖИМ РЕАЛЬНОЙ ТОРГОВЛИ: заявки будут отправляться брокеру")

    # ========== АВТОМАТИЧЕСКИЕ МЕТОДЫ ==========

    def get_capital_mode(self, capital: float) -> dict:
        """Определение режима по капиталу"""
        for mode_name, mode_config in self.CAPITAL_MODES.items():
            if mode_config['min'] <= capital < mode_config['max']:
                return mode_config
        return self.CAPITAL_MODES['full']

    def get_trading_style_name(self, capital: float) -> str:
        """Определение стиля торговли по капиталу"""
        if capital < 3000:
            return "micro"
        elif capital < 10000:
            return "small"
        elif capital < 50000:
            return "medium"
        else:
            return "large"

    def get_adaptive_position_size(self, capital: float) -> float:
        """Адаптивный размер позиции (% от капитала)"""
        mode = self.get_capital_mode(capital)
        return mode['position_pct']

    def get_adaptive_max_positions(self, capital: float) -> int:
        """Адаптивное максимальное количество позиций"""
        mode = self.get_capital_mode(capital)
        return mode['max_positions']

    def get_adaptive_stop_loss(self, capital: float) -> float:
        """Адаптивный стоп-лосс (%)"""
        mode = self.get_capital_mode(capital)
        base_sl = mode['stop_loss_pct']

        # Корректировка по волатильности
        if self.market_volatility > 0.02:
            return base_sl * 1.3
        elif self.market_volatility < 0.005:
            return base_sl * 0.7
        return base_sl

    def get_adaptive_take_profit(self, capital: float) -> float:
        """Адаптивный тейк-профит (%)"""
        mode = self.get_capital_mode(capital)
        base_tp = mode['take_profit_pct']

        # Корректировка по волатильности
        if self.market_volatility > 0.02:
            return base_tp * 1.2
        elif self.market_volatility < 0.005:
            return base_tp * 0.8
        return base_tp

    def get_adaptive_trailing(self, capital: float) -> float:
        """Адаптивный трейлинг-стоп (%)"""
        mode = self.get_capital_mode(capital)
        return mode.get('trailing_pct', 0.4)

    def get_adaptive_timeout(self, capital: float) -> int:
        """Адаптивный таймаут (минуты)"""
        mode = self.get_capital_mode(capital)
        return mode.get('timeout_minutes', 30)

    def get_adaptive_cycle(self, capital: float) -> int:
        """Адаптивный интервал цикла (секунды)"""
        mode = self.get_capital_mode(capital)
        return mode.get('cycle_seconds', 30)

    def get_adaptive_long_threshold(self, capital: float) -> float:
        """Адаптивный порог входа LONG (1-10)"""
        mode = self.get_capital_mode(capital)
        if not mode['can_trade']:
            return 10
        base_threshold = mode['min_confidence']

        # Корректировка по волатильности
        if self.market_volatility > 0.02:
            return min(10, base_threshold + 1)  # Выше волатильность - выше порог
        return base_threshold

    def get_adaptive_short_threshold(self, capital: float) -> float:
        """Адаптивный порог входа SHORT (от -1 до -10)"""
        mode = self.get_capital_mode(capital)

        # SHORT разрешён только в нормальном и полном режимах
        if not mode['can_trade'] or not mode.get('short_allowed', False):
            return -20  # Недоступен

        base_threshold = -mode['min_confidence']

        # Корректировка по волатильности
        if self.market_volatility > 0.02:
            return base_threshold - 1  # Выше волатильность - ниже порог (легче шортить)
        return base_threshold

    def get_adaptive_min_trade_amount(self, capital: float) -> int:
        """Адаптивная минимальная сумма сделки"""
        mode = self.get_capital_mode(capital)
        base_amount = int(mode.get('min_trade_amount', self.min_trade_amount))

        # Адаптация под волатильность
        if self.market_volatility > 0.02:
            return int(base_amount * 0.8)  # При высокой волатильности можно меньше
        return base_amount

    def get_adaptive_vwap_threshold(self, capital: float) -> float:
        """Адаптивный порог VWAP для SHORT"""
        mode = self.get_capital_mode(capital)
        if not mode.get('short_allowed', False):
            return 999  # Недоступен

        # Чем больше капитал, тем выше порог (безопаснее)
        if capital < 10000:
            return 1.01
        elif capital < 30000:
            return 1.02
        else:
            return 1.03

    def get_adaptive_volume_spike(self, capital: float) -> float:
        """Адаптивный порог объёма для SHORT"""
        mode = self.get_capital_mode(capital)
        if not mode.get('short_allowed', False):
            return 999  # Недоступен

        # Адаптация под капитал
        if capital < 10000:
            return 1.8
        elif capital < 30000:
            return 2.0
        else:
            return 2.2

    def is_trading_allowed_by_capital(self, capital: float) -> Tuple[bool, str]:
        """Проверка, можно ли торговать с данным капиталом"""
        mode = self.get_capital_mode(capital)
        if not mode['can_trade']:
            return False, mode['message']
        if capital < self.micro_capital_warning:
            return True, f"⚠️ микро-капитал {capital:.0f}₽"
        return True, "OK"

    def can_use_margin(self, total_capital: float) -> bool:
        """Можно ли использовать маржу"""
        return total_capital >= 5000

    # ========== АВТОМАТИЧЕСКОЕ УПРАВЛЕНИЕ SHORT ==========

    def update_short_settings(self, total_capital: float) -> Tuple[bool, str]:
        """
        АВТОМАТИЧЕСКОЕ обновление настроек SHORT

        Args:
            total_capital: Текущий капитал

        Returns:
            (is_short_enabled, reason)
        """
        mode = self.get_capital_mode(total_capital)

        # Проверка: разрешён ли SHORT в этом режиме
        short_allowed_by_mode = mode.get('short_allowed', False)

        # Проверка: достаточно ли капитала
        short_allowed_by_capital = total_capital >= 7000  # Минимум для SHORT

        new_short_state = short_allowed_by_mode and short_allowed_by_capital

        # Обновляем параметры SHORT
        if new_short_state:
            self.short_vwap_threshold = self.get_adaptive_vwap_threshold(total_capital)
            self.short_volume_spike = self.get_adaptive_volume_spike(total_capital)
            self.short_score_threshold = self.get_adaptive_short_threshold(total_capital)

        # Логируем изменения
        if new_short_state and not self.use_short:
            self.use_short = True
            msg = f"✅🔻 SHORT АВТОМАТИЧЕСКИ ВКЛЮЧЁН! Капитал: {total_capital:.0f}₽ | " \
                  f"Порог: score ≤ {self.short_score_threshold} | " \
                  f"VWAP: {self.short_vwap_threshold:.2f} | " \
                  f"Объём: {self.short_volume_spike:.1f}x"
            info(msg)
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
                msg += f" | Нужно ≥ 7000₽"
            warning(msg)
            return False, msg

        elif new_short_state and self.use_short:
            # SHORT уже включён, обновляем параметры
            self.short_vwap_threshold = self.get_adaptive_vwap_threshold(total_capital)
            self.short_volume_spike = self.get_adaptive_volume_spike(total_capital)
            self.short_score_threshold = self.get_adaptive_short_threshold(total_capital)

        return self.use_short, "OK"

    def emergency_disable_short(self, margin_rate: float, consecutive_losses: int = 0) -> Tuple[bool, str]:
        """
        Экстренное отключение SHORT при рисках

        Args:
            margin_rate: Текущая маржа (%)
            consecutive_losses: Количество убыточных сделок подряд

        Returns:
            (should_disable, reason)
        """
        if not self.use_short:
            return False, ""

        # Критическая маржа
        if margin_rate > 85:
            return True, f"КРИТИЧЕСКАЯ МАРЖА {margin_rate:.1f}% > 85%"

        # Высокая маржа
        if margin_rate > 70:
            return True, f"высокая маржа {margin_rate:.1f}% > 70%"

        # Серия убытков
        if consecutive_losses >= 5:
            return True, f"серия убытков {consecutive_losses} подряд"

        # Серия убытков (предупреждение)
        if consecutive_losses >= 3:
            warning(f"⚠️ Серия убытков {consecutive_losses}, SHORT может быть отключён при следующем")

        return False, ""

    def update_market_conditions(self, volatility: float, trend: float):
        """
        Обновление рыночных условий

        Args:
            volatility: Волатильность (0-1)
            trend: Тренд (-1 до 1, где -1=медвежий, 1=бычий)
        """
        self.market_volatility = volatility
        self.market_trend = trend
        debug(f"📊 Рынок: волатильность={volatility*100:.2f}%, тренд={trend:.2f}")


# ========== ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР ==========
config = TradingConfig()