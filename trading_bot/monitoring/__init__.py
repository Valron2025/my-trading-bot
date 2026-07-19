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
            # ✅ Передаём словарь компонентов
            components_to_monitor = {
                "api_client": bot,
                "position_manager": None,
                "telegram": None,
                "bot": bot
            }
            # Пытаемся получить реальные компоненты
            try:
                from trading_bot.risk.position_manager import position_manager
                components_to_monitor["position_manager"] = position_manager
            except:
                pass
            try:
                from trading_bot.telegram.telegram_notifier import get_telegram_notifier
                components_to_monitor["telegram"] = get_telegram_notifier()
            except:
                pass

            _health_monitor = HealthMonitor(components=components_to_monitor, check_interval=60)
            logger.info("✅ HealthMonitor инициализирован")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось инициализировать HealthMonitor: {e}")
            _health_monitor = None
    return _health_monitor


def get_watchdog(bot=None, check_interval: int = 60) -> Optional[Watchdog]:
    """Получение глобального экземпляра Watchdog"""
    global _watchdog
    if _watchdog is None and bot is not None:
        try:
            _watchdog = Watchdog(
                bot=bot,
                check_interval=check_interval,
                max_idle_cycles=30,
                max_memory_mb=2000,
                max_cpu_percent=90
            )
            logger.info(f"✅ Watchdog инициализирован (интервал={check_interval}с)")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось инициализировать Watchdog: {e}")
            _watchdog = None
    return _watchdog


def init_monitoring(bot=None, prometheus_port: int = 8001,
                    watchdog_interval: int = 60,
                    watchdog_timeout: int = None) -> dict:
    """Инициализация всех компонентов мониторинга"""
    # Если передан старый параметр watchdog_timeout, используем его
    if watchdog_timeout is not None:
        watchdog_interval = max(10, watchdog_timeout // 5)
        logger.info(f"   🔄 Конвертирован watchdog_timeout={watchdog_timeout} → interval={watchdog_interval}")

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

        watchdog = get_watchdog(bot, check_interval=watchdog_interval)
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
            _watchdog.stop_watchdog()  # ← переименовано с stop на stop_watchdog
            logger.info("   ✅ Watchdog остановлен")
        except Exception as e:
            logger.warning(f"   ⚠️ Ошибка остановки Watchdog: {e}")
        _watchdog = None

    if _health_monitor:
        try:
            import asyncio
            if _health_monitor._running:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(_health_monitor.shutdown())
                loop.close()
            logger.info("   ✅ HealthMonitor остановлен")
        except Exception as e:
            logger.warning(f"   ⚠️ Ошибка остановки HealthMonitor: {e}")
        _health_monitor = None

    logger.info("✅ Мониторинг остановлен")


def get_monitoring_status() -> dict:
    """Получение статуса всех компонентов мониторинга"""
    memory_mb = None
    if _memory_monitor:
        try:
            memory_mb = _memory_monitor.get_usage_mb()
        except:
            memory_mb = None

    watchdog_alive = None
    if _watchdog:
        watchdog_alive = _watchdog._running

    return {
        'drawdown_tracker': _drawdown_tracker is not None,
        'memory_monitor': _memory_monitor is not None,
        'prometheus_metrics': _prometheus_metrics is not None,
        'health_monitor': _health_monitor is not None,
        'watchdog': _watchdog is not None,
        'stats': {
            'memory_mb': memory_mb,
            'watchdog_alive': watchdog_alive,
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