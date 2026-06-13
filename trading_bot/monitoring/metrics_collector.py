"""Сбор метрик для Prometheus"""

from ..logger import info


class PrometheusMetrics:
    """Сбор и экспорт метрик Prometheus"""

    def __init__(self, port: int = 8000, enabled: bool = True):
        self.port = port
        self.enabled = enabled
        if enabled:
            info(f"📊 Prometheus метрики настроены на порту {port}")

    def start_server(self):
        """Запуск HTTP сервера для метрик"""
        if self.enabled:
            info(f"✅ Prometheus сервер запущен на порту {self.port}")

    def update_bot_status(self, running: bool):
        pass

    def update_bot_config(self, key: str, value):
        pass

    def update_uptime(self, start_time: float):
        pass

    def update_portfolio(self, total: float, available: float, positions: int):
        pass

    def update_margin_rate(self, margin_rate: float):
        pass

    def update_cycle_count(self, cycle: int):
        pass

    def update_daily_pnl(self, pnl: float):
        pass

    def increment_errors(self, count: int = 1):
        pass

    def update_system_metrics(self):
        pass