"""Circuit Breaker для защиты от каскадных ошибок"""

from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional

from ..logger import info, warning, debug


class CircuitBreakerState(Enum):
    """Состояние предохранителя"""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit Breaker для защиты системы от каскадных ошибок"""

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        half_open_max_calls: int = 1
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[datetime] = None
        self._half_open_success_count = 0

        self._total_requests = 0
        self._successful_requests = 0
        self._total_failures = 0
        self._total_rejects = 0
        self._total_recoveries = 0

        info(f"🔌 Circuit Breaker '{name}' инициализирован (порог={failure_threshold}, таймаут={recovery_timeout}c)")

    @property
    def state(self) -> CircuitBreakerState:
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def can_execute(self) -> bool:
        """Проверка, можно ли выполнить операцию"""
        self._total_requests += 1

        if self._state == CircuitBreakerState.OPEN:
            if self._last_failure_time:
                elapsed = (datetime.now() - self._last_failure_time).total_seconds()
                if elapsed >= self.recovery_timeout:
                    debug(f"🔄 Circuit Breaker '{self.name}' переходит в HALF_OPEN")
                    self._state = CircuitBreakerState.HALF_OPEN
                    self._half_open_success_count = 0
                    self._total_recoveries += 1
                    return True
            debug(f"🚫 Circuit Breaker '{self.name}' OPEN, запрос отклонён")
            self._total_rejects += 1
            return False

        if self._state == CircuitBreakerState.HALF_OPEN:
            return self._half_open_success_count < self.half_open_max_calls

        return True

    def record_success(self) -> None:
        """Запись успешного выполнения"""
        self._successful_requests += 1

        if self._state == CircuitBreakerState.HALF_OPEN:
            self._half_open_success_count += 1
            if self._half_open_success_count >= self.half_open_max_calls:
                info(f"✅ Circuit Breaker '{self.name}' переходит в CLOSED")
                self._state = CircuitBreakerState.CLOSED
        elif self._state != CircuitBreakerState.CLOSED:
            self._state = CircuitBreakerState.CLOSED

        self._failure_count = 0
        self._last_failure_time = None

    def record_failure(self) -> None:
        """Запись неудачного выполнения"""
        self._failure_count += 1
        self._total_failures += 1
        self._last_failure_time = datetime.now()

        if self._state == CircuitBreakerState.HALF_OPEN:
            warning(f"⚠️ Circuit Breaker '{self.name}' HALF_OPEN -> OPEN (ошибка в тесте)")
            self._state = CircuitBreakerState.OPEN
            self._half_open_success_count = 0
        elif self._failure_count >= self.failure_threshold and self._state != CircuitBreakerState.OPEN:
            warning(f"🚨 Circuit Breaker '{self.name}' переходит в OPEN после {self._failure_count} ошибок")
            self._state = CircuitBreakerState.OPEN

    def reset(self) -> None:
        """Сброс состояния"""
        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._last_failure_time = None
        self._half_open_success_count = 0
        info(f"🔄 Circuit Breaker '{self.name}' сброшен в CLOSED")

    def get_status(self) -> Dict[str, Any]:
        """Получение статуса"""
        return {
            'name': self.name,
            'state': self._state.value,
            'failure_count': self._failure_count,
            'failure_threshold': self.failure_threshold,
            'recovery_timeout': self.recovery_timeout,
            'half_open_max_calls': self.half_open_max_calls,
            'total_requests': self._total_requests,
            'successful_requests': self._successful_requests,
            'total_failures': self._total_failures,
            'total_rejects': self._total_rejects,
            'total_recoveries': self._total_recoveries,
            'success_rate': round(self._successful_requests / max(1, self._total_requests) * 100, 2),
            'last_failure_time': self._last_failure_time.isoformat() if self._last_failure_time else None
        }

    def __enter__(self):
        if not self.can_execute():
            raise Exception(f"Circuit Breaker '{self.name}' is OPEN")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.record_success()
        else:
            self.record_failure()


class CircuitBreakerRegistry:
    """Реестр Circuit Breaker для управления несколькими экземплярами"""

    _instance = None
    _breakers: Dict[str, CircuitBreaker] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get(cls, name: str, **kwargs) -> CircuitBreaker:
        """Получение или создание Circuit Breaker"""
        if name not in cls._breakers:
            cls._breakers[name] = CircuitBreaker(name, **kwargs)
        return cls._breakers[name]

    @classmethod
    def get_all_status(cls) -> Dict[str, Dict]:
        """Статус всех Circuit Breaker"""
        return {name: cb.get_status() for name, cb in cls._breakers.items()}

    @classmethod
    def reset_all(cls) -> None:
        """Сброс всех Circuit Breaker"""
        for cb in cls._breakers.values():
            cb.reset()
        info("🔄 Все Circuit Breaker сброшены")

    @classmethod
    def reset(cls, name: str) -> bool:
        """Сброс конкретного Circuit Breaker"""
        if name in cls._breakers:
            cls._breakers[name].reset()
            return True
        return False


# Глобальные экземпляры
api_circuit_breaker = CircuitBreakerRegistry.get("api", failure_threshold=3, recovery_timeout=60)
trading_circuit_breaker = CircuitBreakerRegistry.get("trading", failure_threshold=5, recovery_timeout=120)
margin_circuit_breaker = CircuitBreakerRegistry.get("margin", failure_threshold=2, recovery_timeout=300)