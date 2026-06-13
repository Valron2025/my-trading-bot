#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
prometheus_metrics.py - Метрики для мониторинга в Prometheus
"""

import time
import logging
from typing import Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Отложенный импорт logger для избежания циклических зависимостей
_info = None
_error = None
_debug = None


def _get_logger():
    """Отложенный импорт функций логирования"""
    global _info, _error, _debug
    if _info is None:
        try:
            from trading_bot.logger import info, error, debug
            _info, _error, _debug = info, error, debug
        except ImportError:
            try:
                from ..logger import info, error, debug
                _info, _error, _debug = info, error, debug
            except ImportError:
                # Fallback - заглушки
                def noop(*args, **kwargs): pass
                _info = _error = _debug = noop
    return _info, _error, _debug


def info(msg): return _get_logger()[0](msg)
def error(msg): return _get_logger()[1](msg)
def debug(msg): return _get_logger()[2](msg)


# Попытка импорта prometheus_client
try:
    from prometheus_client import (
        start_http_server, Gauge, Counter, Histogram,
        start_http_server, Gauge, Counter, Histogram,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    print("⚠️ prometheus_client не установлен. Установите: pip install prometheus-client")


class PrometheusMetrics:
    """Сбор и экспорт метрик в Prometheus - SINGLETON"""

    _instance = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, port: int = 8000, enabled: bool = True):
        if self._initialized:
            return

        self.port = port
        self.enabled = enabled and PROMETHEUS_AVAILABLE
        self._started = False
        self._config_metrics = {}
        self._initialized = True

        if self.enabled:
            self._init_metrics()
            info(f"✅ PrometheusMetrics инициализирован (порт: {port})")
        else:
            info("ℹ️ PrometheusMetrics отключен")

    def _init_metrics(self):
        """Инициализация всех метрик"""
        self.bot_status = Gauge('bot_status', 'Bot status (0=stopped, 1=running)')
        self.bot_uptime = Gauge('bot_uptime_seconds', 'Bot uptime in seconds')
        self.bot_errors_total = Counter('bot_errors_total', 'Total number of errors')

        self.portfolio_value = Gauge('portfolio_value', 'Total portfolio value')
        self.portfolio_cash = Gauge('portfolio_cash', 'Cash balance')
        self.portfolio_positions = Gauge('portfolio_positions_count', 'Number of positions')
        self.daily_pnl = Gauge('daily_pnl', 'Daily profit/loss')
        self.margin_rate = Gauge('margin_rate_percent', 'Margin usage percentage')

        self.trades_total = Counter('trades_total', 'Total number of trades')
        self.trades_buy = Counter('trades_buy', 'Number of buy trades')
        self.trades_sell = Counter('trades_sell', 'Number of sell trades')
        self.trade_volume = Counter('trade_volume_rub', 'Total trading volume in RUB')

        self.api_requests = Counter('api_requests_total', 'Total API requests')
        self.api_errors = Counter('api_errors_total', 'Total API errors')
        self.api_latency = Histogram('api_latency_seconds', 'API request latency')

        self.memory_usage = Gauge('memory_usage_bytes', 'Memory usage in bytes')
        self.cpu_usage = Gauge('cpu_usage_percent', 'CPU usage percentage')
        self.cycle_count = Gauge('trading_cycle_count', 'Number of trading cycles')

    def start_server(self):
        """Запуск HTTP сервера для Prometheus"""
        if not self.enabled or self._started:
            return
        try:
            start_http_server(self.port)
            self._started = True
            info(f"🚀 Prometheus сервер запущен на порту {self.port}")
        except Exception as e:
            error(f"❌ Ошибка запуска Prometheus сервера: {e}")

    def update_bot_status(self, running: bool):
        if not self.enabled:
            return
        self.bot_status.set(1 if running else 0)

    def update_uptime(self, start_time):
        if not self.enabled:
            return
        try:
            if isinstance(start_time, datetime):
                uptime = (datetime.now() - start_time).total_seconds()
            elif isinstance(start_time, (int, float)):
                uptime = time.time() - start_time
            else:
                return
            self.bot_uptime.set(uptime)
        except Exception:
            pass

    def increment_errors(self, count: int = 1):
        if not self.enabled:
            return
        self.bot_errors_total.inc(count)

    def update_portfolio(self, value: float, cash: float, positions_count: int):
        if not self.enabled:
            return
        self.portfolio_value.set(value)
        self.portfolio_cash.set(cash)
        self.portfolio_positions.set(positions_count)

    def update_margin_rate(self, rate: float):
        if not self.enabled:
            return
        self.margin_rate.set(rate)

    def update_daily_pnl(self, pnl: float):
        if not self.enabled:
            return
        self.daily_pnl.set(pnl)

    def update_cycle_count(self, count: int):
        if not self.enabled:
            return
        self.cycle_count.set(count)

    def record_trade(self, direction: str, volume: float):
        if not self.enabled:
            return
        self.trades_total.inc()
        if direction.lower() == 'buy':
            self.trades_buy.inc()
        else:
            self.trades_sell.inc()
        self.trade_volume.inc(volume)

    def record_api_request(self, latency: float, success: bool = True):
        if not self.enabled:
            return
        self.api_requests.inc()
        self.api_latency.observe(latency)
        if not success:
            self.api_errors.inc()

    def update_system_metrics(self):
        if not self.enabled:
            return
        try:
            import psutil
            process = psutil.Process()
            self.memory_usage.set(process.memory_info().rss)
            self.cpu_usage.set(process.cpu_percent())
        except Exception:
            pass

    def get_metrics_summary(self) -> Dict[str, Any]:
        return {
            'prometheus_enabled': self.enabled,
            'port': self.port,
            'started': self._started
        }


# Создаём глобальный экземпляр
metrics = PrometheusMetrics()