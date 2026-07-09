#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
prometheus_metrics.py - Метрики для мониторинга в Prometheus
РАБОЧАЯ ВЕРСИЯ - БЕЗ РЕКУРСИИ И ДУБЛИРОВАНИЯ
"""

import time
import logging
import threading
from typing import Dict, Any, Optional
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
                def noop(*args, **kwargs): pass
                _info = _error = _debug = noop
    return _info, _error, _debug


def info(msg): return _get_logger()[0](msg)
def error(msg): return _get_logger()[1](msg)
def debug(msg): return _get_logger()[2](msg)


# Попытка импорта prometheus_client
try:
    from prometheus_client import (
        start_http_server,
        Gauge,
        Counter,
        Histogram,
        CollectorRegistry,
        REGISTRY,
        generate_latest,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    print("⚠️ prometheus_client не установлен. Установите: pip install prometheus-client")


class PrometheusMetrics:
    """Сбор и экспорт метрик в Prometheus - SINGLETON"""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, port: int = 8000, enabled: bool = True):
        if hasattr(self, '_initialized'):
            return

        self.port = port
        self.enabled = enabled and PROMETHEUS_AVAILABLE
        self._started = False
        self._start_time = time.time()
        self._lock = threading.Lock()
        self._initialized = False
        self._metrics_created = False

        if self.enabled:
            try:
                self._init_metrics()
                self._initialized = True
                info(f"✅ PrometheusMetrics инициализирован (порт: {port})")
            except Exception as e:
                error(f"⚠️ Ошибка инициализации Prometheus: {e}")
                self.enabled = False
                self._initialized = False
        else:
            info("ℹ️ PrometheusMetrics отключен")

        self._initialized = True

    def _init_metrics(self):
        """Инициализация всех метрик - БЕЗ РЕКУРСИИ"""
        if self._metrics_created:
            return

        try:
            # ========== БОТ ==========
            self.bot_status = Gauge('bot_status', 'Bot status (0=stopped, 1=running)')
            self.bot_uptime = Gauge('bot_uptime_seconds', 'Bot uptime in seconds')
            self.bot_errors_total = Counter('bot_errors_total', 'Total number of errors')

            # ========== ПОРТФЕЛЬ ==========
            self.portfolio_value = Gauge('portfolio_value', 'Total portfolio value')
            self.portfolio_cash = Gauge('portfolio_cash', 'Cash balance')
            self.portfolio_positions = Gauge('portfolio_positions_count', 'Number of positions')
            self.daily_pnl = Gauge('daily_pnl', 'Daily profit/loss')
            self.margin_rate = Gauge('margin_rate_percent', 'Margin usage percentage')

            # ========== ТОРГОВЛЯ ==========
            self.trades_total = Counter('trades_total', 'Total number of trades')
            self.trades_buy = Counter('trades_buy', 'Number of buy trades')
            self.trades_sell = Counter('trades_sell', 'Number of sell trades')
            self.trade_volume = Counter('trade_volume_rub', 'Total trading volume in RUB')
            self.cycle_count = Gauge('trading_cycle_count', 'Number of trading cycles')

            # ========== API ==========
            self.api_requests = Counter('api_requests_total', 'Total API requests')
            self.api_errors = Counter('api_errors_total', 'Total API errors')
            self.api_latency = Histogram(
                'api_latency_seconds',
                'API request latency',
                buckets=[0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0]
            )

            # ========== СИСТЕМА ==========
            self.memory_usage = Gauge('memory_usage_bytes', 'Memory usage in bytes')
            self.cpu_usage = Gauge('cpu_usage_percent', 'CPU usage percentage')

            # ========== КЭШ ==========
            self.cache_hits = Gauge('cache_hits', 'Cache hits', ['cache'])
            self.cache_misses = Gauge('cache_misses', 'Cache misses', ['cache'])
            self.cache_size = Gauge('cache_size', 'Cache size', ['cache'])

            self._metrics_created = True

        except ValueError as e:
            if "Duplicated" in str(e):
                error(f"⚠️ Дублирование метрик в реестре: {e}")
                error("   Используйте существующие метрики")
                # Помечаем как созданные, чтобы не пытаться создать снова
                self._metrics_created = True
            else:
                raise

    # ========== МЕТОДЫ ДЛЯ ОБНОВЛЕНИЯ МЕТРИК ==========

    def _check_and_create(self):
        """Проверка и создание метрик если необходимо"""
        if not self._metrics_created and self.enabled:
            try:
                self._init_metrics()
            except Exception as e:
                debug(f"Ошибка создания метрик: {e}")

    def set_bot_status(self, status: int):
        if not self.enabled:
            return
        self._check_and_create()
        try:
            with self._lock:
                self.bot_status.set(float(status))
        except Exception as e:
            debug(f"Ошибка set_bot_status: {e}")

    def set_bot_uptime(self, uptime: float):
        if not self.enabled:
            return
        self._check_and_create()
        try:
            with self._lock:
                self.bot_uptime.set(uptime)
        except Exception as e:
            debug(f"Ошибка set_bot_uptime: {e}")

    def set_portfolio_value(self, value: float):
        if not self.enabled:
            return
        self._check_and_create()
        try:
            with self._lock:
                self.portfolio_value.set(value)
        except Exception as e:
            debug(f"Ошибка set_portfolio_value: {e}")

    def set_portfolio_cash(self, cash: float):
        if not self.enabled:
            return
        self._check_and_create()
        try:
            with self._lock:
                self.portfolio_cash.set(cash)
        except Exception as e:
            debug(f"Ошибка set_portfolio_cash: {e}")

    def set_positions_count(self, count: float):
        if not self.enabled:
            return
        self._check_and_create()
        try:
            with self._lock:
                self.portfolio_positions.set(count)
        except Exception as e:
            debug(f"Ошибка set_positions_count: {e}")

    def set_daily_pnl(self, pnl: float):
        if not self.enabled:
            return
        self._check_and_create()
        try:
            with self._lock:
                self.daily_pnl.set(pnl)
        except Exception as e:
            debug(f"Ошибка set_daily_pnl: {e}")

    def set_margin_rate(self, rate: float):
        if not self.enabled:
            return
        self._check_and_create()
        try:
            with self._lock:
                self.margin_rate.set(rate)
        except Exception as e:
            debug(f"Ошибка set_margin_rate: {e}")

    def set_trading_cycle_count(self, count: int):
        if not self.enabled:
            return
        self._check_and_create()
        try:
            with self._lock:
                self.cycle_count.set(float(count))
        except Exception as e:
            debug(f"Ошибка set_trading_cycle_count: {e}")

    def update_bot_status(self, running: bool = None, cycle_count: int = None,
                          positions: int = None, capital: float = None):
        if not self.enabled:
            return
        self._check_and_create()
        try:
            with self._lock:
                if running is not None:
                    self.bot_status.set(1 if running else 0)
                if cycle_count is not None:
                    self.cycle_count.set(cycle_count)
                if positions is not None:
                    self.portfolio_positions.set(positions)
                if capital is not None:
                    self.portfolio_value.set(capital)
        except Exception as e:
            debug(f"Ошибка update_bot_status: {e}")

    def update_uptime(self, start_time):
        if not self.enabled:
            return
        self._check_and_create()
        try:
            if isinstance(start_time, datetime):
                uptime = (datetime.now() - start_time).total_seconds()
            elif isinstance(start_time, (int, float)):
                uptime = time.time() - start_time
            else:
                return
            with self._lock:
                self.bot_uptime.set(uptime)
        except Exception as e:
            debug(f"Ошибка update_uptime: {e}")

    def increment_errors(self, count: int = 1):
        if not self.enabled:
            return
        self._check_and_create()
        try:
            with self._lock:
                self.bot_errors_total.inc(count)
        except Exception as e:
            debug(f"Ошибка increment_errors: {e}")

    def update_portfolio(self, value: float, cash: float, positions_count: int):
        if not self.enabled:
            return
        self._check_and_create()
        try:
            with self._lock:
                self.portfolio_value.set(value)
                self.portfolio_cash.set(cash)
                self.portfolio_positions.set(positions_count)
        except Exception as e:
            debug(f"Ошибка update_portfolio: {e}")

    def update_margin_rate(self, rate: float):
        if not self.enabled:
            return
        self._check_and_create()
        try:
            with self._lock:
                self.margin_rate.set(rate)
        except Exception as e:
            debug(f"Ошибка update_margin_rate: {e}")

    def update_daily_pnl(self, pnl: float):
        if not self.enabled:
            return
        self._check_and_create()
        try:
            with self._lock:
                self.daily_pnl.set(pnl)
        except Exception as e:
            debug(f"Ошибка update_daily_pnl: {e}")

    def update_cycle_count(self, count: int):
        if not self.enabled:
            return
        self._check_and_create()
        try:
            with self._lock:
                self.cycle_count.set(count)
        except Exception as e:
            debug(f"Ошибка update_cycle_count: {e}")

    def record_trade(self, direction: str, volume: float):
        if not self.enabled:
            return
        self._check_and_create()
        try:
            with self._lock:
                self.trades_total.inc()
                if direction.lower() == 'buy':
                    self.trades_buy.inc()
                else:
                    self.trades_sell.inc()
                self.trade_volume.inc(volume)
        except Exception as e:
            debug(f"Ошибка record_trade: {e}")

    def record_api_request(self, latency: float, success: bool = True):
        if not self.enabled:
            return
        self._check_and_create()
        try:
            with self._lock:
                self.api_requests.inc()
                self.api_latency.observe(latency)
                if not success:
                    self.api_errors.inc()
        except Exception as e:
            debug(f"Ошибка record_api_request: {e}")

    def update_system_metrics(self):
        if not self.enabled:
            return
        self._check_and_create()
        try:
            import psutil
            process = psutil.Process()
            with self._lock:
                self.memory_usage.set(process.memory_info().rss)
                self.cpu_usage.set(process.cpu_percent(interval=0.5))
        except Exception as e:
            debug(f"Ошибка update_system_metrics: {e}")

    def start_server(self):
        if not self.enabled or self._started:
            return
        try:
            start_http_server(self.port)
            self._started = True
            info(f"🚀 Prometheus сервер запущен на порту {self.port}")
        except Exception as e:
            error(f"❌ Ошибка запуска Prometheus сервера: {e}")

    def get_metrics(self) -> str:
        if not self.enabled:
            return "# Prometheus disabled\n"
        self._check_and_create()
        try:
            return generate_latest().decode('utf-8')
        except Exception as e:
            return f"# Error generating metrics: {e}\n"

    def get_metrics_summary(self) -> Dict[str, Any]:
        return {
            'prometheus_enabled': self.enabled,
            'port': self.port,
            'started': self._started,
            'uptime_seconds': time.time() - self._start_time,
            'metrics_created': self._metrics_created,
            'initialized': self._initialized,
        }


# ========== ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР ==========
metrics = PrometheusMetrics()
prometheus_metrics = metrics