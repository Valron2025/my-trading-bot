#!/usr/bin/env python3
"""
metrics_collector.py - Периодический сбор метрик со всех компонентов
"""

import time
import sqlite3
from typing import Dict, Any, Optional
from trading_bot.logger import info, debug


class MetricsCollector:
    """Сбор метрик со всех компонентов"""

    def __init__(self, bot):
        self.bot = bot
        self._last_collection = 0
        self._collection_interval = 60  # 1 минута

    async def collect_metrics(self):
        """Сбор всех метрик"""
        now = time.time()
        if now - self._last_collection < self._collection_interval:
            return

        debug("📊 Collecting metrics...")

        # Сбор метрик кэша
        await self._collect_cache_metrics()

        # Сбор метрик бэктеста
        await self._collect_backtest_metrics()

        # Сбор метрик БД
        await self._collect_database_metrics()

        self._last_collection = now
        debug("✅ Metrics collection completed")

    async def _collect_cache_metrics(self):
        """Сбор метрик кэша"""
        try:
            from trading_bot.cache import (
                price_cache, positions_cache, candles_cache,
                margin_cache, instruments_cache
            )
            from trading_bot.monitoring.prometheus_metrics import get_prometheus_metrics

            metrics = get_prometheus_metrics()
            if not metrics:
                return

            for name, cache in [
                ("price", price_cache),
                ("positions", positions_cache),
                ("candles", candles_cache),
                ("margin", margin_cache),
                ("instruments", instruments_cache),
            ]:
                if hasattr(cache, 'get_stats'):
                    stats = cache.get_stats()
                    if hasattr(metrics, 'update_cache_metrics'):
                        metrics.update_cache_metrics(
                            cache_name=name,
                            hits=stats.get('hits', 0),
                            misses=stats.get('misses', 0),
                            size=stats.get('size', 0)
                        )
                        debug(
                            f"   📦 {name} cache: hit_rate={stats.get('hit_rate', 0):.1f}%, size={stats.get('size', 0)}")
        except Exception as e:
            debug(f"Error collecting cache metrics: {e}")

    async def _collect_backtest_metrics(self):
        """Сбор метрик бэктеста из БД"""
        if hasattr(self.bot, 'db') and self.bot.db:
            try:
                from trading_bot.monitoring.prometheus_metrics import get_prometheus_metrics

                conn = sqlite3.connect(self.bot.db.db_path)
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT ticker, profit_factor, win_rate, total_return, sharpe_ratio, total_trades
                    FROM backtest_results 
                    ORDER BY id DESC LIMIT 10
                """)
                rows = cursor.fetchall()
                conn.close()

                metrics = get_prometheus_metrics()
                if metrics:
                    for row in rows:
                        if hasattr(metrics, 'update_backtest_metrics'):
                            metrics.update_backtest_metrics(
                                ticker=row[0],
                                result={
                                    'profit_factor': row[1] if len(row) > 1 else 0,
                                    'win_rate': row[2] if len(row) > 2 else 0,
                                    'total_return': row[3] if len(row) > 3 else 0,
                                    'sharpe_ratio': row[4] if len(row) > 4 else 0,
                                    'total_trades': row[5] if len(row) > 5 else 0
                                }
                            )
                            debug(f"   📊 Backtest metrics for {row[0]}")
            except Exception as e:
                debug(f"Error collecting backtest metrics: {e}")

    async def _collect_database_metrics(self):
        """Сбор метрик БД"""
        if hasattr(self.bot, 'db') and self.bot.db:
            try:
                conn = sqlite3.connect(self.bot.db.db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM positions")
                positions_count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM trades")
                trades_count = cursor.fetchone()[0]
                conn.close()

                from trading_bot.monitoring.prometheus_metrics import get_prometheus_metrics
                metrics = get_prometheus_metrics()
                if metrics and hasattr(metrics, 'db_size'):
                    metrics.db_size.labels(component='positions').set(positions_count)
                    metrics.db_size.labels(component='trades').set(trades_count)
                    debug(f"   💾 DB: positions={positions_count}, trades={trades_count}")
            except Exception as e:
                debug(f"Error collecting DB metrics: {e}")


# Глобальный экземпляр
_metrics_collector: Optional[MetricsCollector] = None


def get_metrics_collector(bot=None) -> Optional[MetricsCollector]:
    """Получение глобального экземпляра MetricsCollector"""
    global _metrics_collector
    if _metrics_collector is None and bot is not None:
        _metrics_collector = MetricsCollector(bot)
    return _metrics_collector


# ========== ДЛЯ ОБРАТНОЙ СОВМЕСТИМОСТИ ==========
# Если кто-то импортирует PrometheusMetrics из этого файла
try:
    from .prometheus_metrics import PrometheusMetrics
except ImportError:
    # Заглушка, если prometheus_metrics не доступен
    class PrometheusMetrics:
        pass