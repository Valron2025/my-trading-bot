"""Monitoring module - мониторинг и метрики"""

import logging
from typing import Optional

from .drawdown_tracker import DrawdownTracker
from .memory_monitor import MemoryMonitor
from .health_monitor import HealthMonitor
from .watchdog import Watchdog
from .prometheus_metrics import PrometheusMetrics
from .metrics_collector import MetricsCollector

# Настройка логгера
logger = logging.getLogger(__name__)

# Глобальные экземпляры (ленивая инициализация)
_drawdown_tracker: Optional[DrawdownTracker] = None
_memory_monitor: Optional[MemoryMonitor] = None
_prometheus_metrics: Optional[PrometheusMetrics] = None
_health_monitor: Optional[HealthMonitor] = None
_watchdog: Optional[Watchdog] = None


def get_drawdown_tracker(bot=None) -> DrawdownTracker:
    """Получение глобального экземпляра DrawdownTracker"""
    global _drawdown_tracker
    if _drawdown_tracker is None and bot is not None:
        _drawdown_tracker = DrawdownTracker(bot)
        logger.info("✅ DrawdownTracker инициализирован")
    return _drawdown_tracker


def get_memory_monitor() -> MemoryMonitor:
    """Получение глобального экземпляра MemoryMonitor"""
    global _memory_monitor
    if _memory_monitor is None:
        _memory_monitor = MemoryMonitor()
        logger.info("✅ MemoryMonitor инициализирован")
    return _memory_monitor


def get_prometheus_metrics(port: int = 8001, enabled: bool = True) -> Optional[PrometheusMetrics]:
    """Получение глобального экземпляра PrometheusMetrics"""
    global _prometheus_metrics
    if _prometheus_metrics is None and enabled:
        try:
            _prometheus_metrics = PrometheusMetrics(port=port, enabled=enabled)
            logger.info(f"✅ PrometheusMetrics инициализирован на порту {port}")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось инициализировать PrometheusMetrics: {e}")
            _prometheus_metrics = None
    return _prometheus_metrics


def get_health_monitor(bot=None) -> Optional[HealthMonitor]:
    """Получение глобального экземпляра HealthMonitor"""
    global _health_monitor
    if _health_monitor is None and bot is not None:
        try:
            _health_monitor = HealthMonitor(bot)
            logger.info("✅ HealthMonitor инициализирован")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось инициализировать HealthMonitor: {e}")
            _health_monitor = None
    return _health_monitor


def get_watchdog(bot=None, timeout_seconds: int = 300) -> Optional[Watchdog]:
    """Получение глобального экземпляра Watchdog"""
    global _watchdog
    if _watchdog is None and bot is not None:
        try:
            _watchdog = Watchdog(bot, timeout_seconds=timeout_seconds)
            logger.info(f"✅ Watchdog инициализирован (таймаут={timeout_seconds}с)")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось инициализировать Watchdog: {e}")
            _watchdog = None
    return _watchdog


def init_monitoring(bot=None, prometheus_port: int = 8001, watchdog_timeout: int = 300) -> dict:
    """Инициализация всех компонентов мониторинга"""
    results = {
        'drawdown_tracker': False,
        'memory_monitor': False,
        'prometheus': False,
        'health_monitor': False,
        'watchdog': False
    }

    logger.info("📊 Инициализация системы мониторинга...")

    if bot:
        tracker = get_drawdown_tracker(bot)
        if tracker:
            results['drawdown_tracker'] = True

        health = get_health_monitor(bot)
        if health:
            results['health_monitor'] = True

        watchdog = get_watchdog(bot, timeout_seconds=watchdog_timeout)
        if watchdog:
            results['watchdog'] = True

    memory = get_memory_monitor()
    if memory:
        results['memory_monitor'] = True

    prometheus = get_prometheus_metrics(port=prometheus_port, enabled=True)
    if prometheus:
        results['prometheus'] = True

    logger.info(f"📊 Мониторинг инициализирован: {results}")
    return results


def shutdown_monitoring():
    """Корректное завершение всех компонентов мониторинга"""
    global _prometheus_metrics, _watchdog, _health_monitor

    logger.info("🛑 Остановка системы мониторинга...")

    if _prometheus_metrics:
        try:
            _prometheus_metrics.stop()
            logger.info("   ✅ PrometheusMetrics остановлен")
        except Exception as e:
            logger.warning(f"   ⚠️ Ошибка остановки PrometheusMetrics: {e}")
        _prometheus_metrics = None

    if _watchdog:
        try:
            _watchdog.stop()
            logger.info("   ✅ Watchdog остановлен")
        except Exception as e:
            logger.warning(f"   ⚠️ Ошибка остановки Watchdog: {e}")
        _watchdog = None

    if _health_monitor:
        try:
            _health_monitor.stop()
            logger.info("   ✅ HealthMonitor остановлен")
        except Exception as e:
            logger.warning(f"   ⚠️ Ошибка остановки HealthMonitor: {e}")
        _health_monitor = None

    logger.info("✅ Мониторинг остановлен")


def get_monitoring_status() -> dict:
    """Получение статуса всех компонентов мониторинга"""
    return {
        'drawdown_tracker': _drawdown_tracker is not None,
        'memory_monitor': _memory_monitor is not None,
        'prometheus_metrics': _prometheus_metrics is not None,
        'health_monitor': _health_monitor is not None,
        'watchdog': _watchdog is not None,
        'stats': {
            'memory_mb': _memory_monitor.get_memory_usage_mb() if _memory_monitor else None,
            'watchdog_alive': _watchdog.is_alive() if _watchdog else None,
        }
    }


__all__ = [
    "DrawdownTracker",
    "MemoryMonitor",
    "PrometheusMetrics",
    "HealthMonitor",
    "Watchdog",
    "MetricsCollector",
    "get_drawdown_tracker",
    "get_memory_monitor",
    "get_prometheus_metrics",
    "get_health_monitor",
    "get_watchdog",
    "init_monitoring",
    "shutdown_monitoring",
    "get_monitoring_status",
]

logger.info("📊 Monitoring module initialized")
