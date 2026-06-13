# health_monitor.py
"""Мониторинг здоровья компонентов системы"""

import asyncio
from datetime import datetime
from typing import Dict, Any, Optional

from ..logger import info, warning, error


class HealthMonitor:
    """Мониторинг здоровья всех компонентов системы"""

    def __init__(self, components: Dict[str, Any], check_interval: int = 60):
        self.components = components
        self.check_interval = check_interval
        self._last_check: Dict[str, datetime] = {}
        self._status: Dict[str, Dict[str, Any]] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None

        info(f"❤️ HealthMonitor инициализирован ({len(components)} компонентов)")

    async def start(self):
        """Запуск мониторинга"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        info("❤️ HealthMonitor запущен")

    async def stop(self):
        """Остановка мониторинга"""
        self._running = False
        if self._task:
            self._task.cancel()
        info("❤️ HealthMonitor остановлен")

    async def _monitor_loop(self):
        """Основной цикл мониторинга"""
        while self._running:
            try:
                await self.check_all()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                error(f"HealthMonitor error: {e}")
                await asyncio.sleep(self.check_interval)

    async def check_all(self) -> Dict[str, Any]:
        """Проверка всех компонентов"""
        results = {}

        for name, component in self.components.items():
            if component is None:
                results[name] = {"status": "unhealthy", "error": "Component is None"}
                continue

            try:
                status = await self._check_component(name, component)
                results[name] = status
                self._status[name] = status
                self._last_check[name] = datetime.now()

            except Exception as e:
                results[name] = {"status": "error", "error": str(e)}
                warning(f"⚠️ Компонент {name}: {str(e)[:100]}")

        healthy_count = sum(1 for r in results.values() if r.get("status") in ["healthy", "ok"])
        total_count = len(results)

        return {
            "timestamp": datetime.now().isoformat(),
            "healthy_count": healthy_count,
            "total_count": total_count,
            "health_percent": round(healthy_count / max(1, total_count) * 100, 2),
            "is_healthy": healthy_count == total_count,
            "components": results
        }

    async def _check_component(self, name: str, component: Any) -> Dict[str, Any]:
        """Проверка одного компонента"""
        result = {"status": "unknown", "checked_at": datetime.now().isoformat()}

        if name == "api_client":
            try:
                from ..api.tbank_client import tbank
                available, total, _ = tbank.get_available_funds()
                if total > 0:
                    result = {"status": "healthy", "balance": total, "available": available}
                else:
                    result = {"status": "warning", "balance": total}
            except Exception as e:
                result = {"status": "unhealthy", "error": str(e)}

        elif name == "position_manager":
            try:
                from ..risk.position_manager import position_manager
                positions = position_manager.get_all_positions()
                result = {"status": "healthy", "positions_count": len(positions)}
            except Exception as e:
                result = {"status": "unhealthy", "error": str(e)}

        elif name == "telegram":
            try:
                from ..telegram.telegram_notifier import get_telegram_notifier
                notifier = get_telegram_notifier()
                if notifier and notifier.enabled:
                    result = {"status": "healthy", "enabled": True}
                else:
                    result = {"status": "warning", "enabled": False}
            except Exception as e:
                result = {"status": "unhealthy", "error": str(e)}

        else:
            if hasattr(component, 'health_check'):
                health = component.health_check()
                result = health if isinstance(health, dict) else {"status": "healthy" if health else "warning"}
            else:
                result = {"status": "unknown", "note": "No health check method"}

        return result

    def get_status(self) -> Dict[str, Any]:
        """Текущий статус (кэшированный)"""
        return {
            "components": self._status,
            "last_checks": {k: v.isoformat() for k, v in self._last_check.items()},
            "timestamp": datetime.now().isoformat()
        }

    def is_healthy(self) -> bool:
        """Общее здоровье системы"""
        if not self._status:
            return False
        unhealthy = [s for s in self._status.values() if s.get("status") not in ["healthy", "ok"]]
        return len(unhealthy) == 0