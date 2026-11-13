import time
from enum import Enum

from app.core.logging import get_logger

logger = get_logger("app.circuit")


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        open_seconds: float = 30.0,
        half_open_probes: int = 1,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.open_seconds = open_seconds
        self.half_open_probes = half_open_probes
        self._state = BreakerState.CLOSED
        self._failure_count = 0
        self._opened_at = 0.0
        self._probes_done = 0

    @property
    def state(self) -> BreakerState:
        if self._state == BreakerState.OPEN:
            if time.monotonic() - self._opened_at >= self.open_seconds:
                self._state = BreakerState.HALF_OPEN
                self._probes_done = 0
        return self._state

    def allow_request(self) -> bool:
        state = self.state
        if state == BreakerState.CLOSED:
            return True
        if state == BreakerState.HALF_OPEN:
            if self._probes_done < self.half_open_probes:
                self._probes_done += 1
                return True
            return False
        return False

    def record_success(self) -> None:
        if self._state == BreakerState.HALF_OPEN:
            logger.info("circuit recovered", extra={"kind": "system", "breaker": self.name})
        self._state = BreakerState.CLOSED
        self._failure_count = 0

    def record_failure(self) -> None:
        self._failure_count += 1
        if self._state == BreakerState.HALF_OPEN:
            self._trip()
            return
        if self._failure_count >= self.failure_threshold:
            self._trip()

    def _trip(self) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = time.monotonic()
        self._failure_count = 0
        logger.warning(
            "circuit opened",
            extra={"kind": "system", "breaker": self.name, "open_seconds": self.open_seconds},
        )


llm_breaker = CircuitBreaker("llm")
amap_breaker = CircuitBreaker("amap")
weather_breaker = CircuitBreaker("weather")