import asyncio
import time
from typing import Any

import httpx

from app.clients.circuit_breaker import CircuitBreaker
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("app.amap")

BASE_URL = "https://restapi.amap.com"

MODE_ENDPOINTS = {
    "公交": "/v5/direction/transit/integrated",
    "步行": "/v5/direction/walking",
    "驾车": "/v5/direction/driving",
}

# 高德 v5 公交路径规划要求 city1/city2 传城市 adcode，传中文城市名会返回 INVALID_PARAMS。
CITY_ADCODE = "320300"
CITY_NAME = "徐州"


class TokenBucket:
    def __init__(self, rate_per_second: float) -> None:
        self.rate = rate_per_second
        self.capacity = max(1.0, rate_per_second)
        self.tokens = self.capacity
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.last_refill) * self.rate)
                self.last_refill = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
            await asyncio.sleep(wait)


_bucket: TokenBucket | None = None


def _get_bucket() -> TokenBucket:
    global _bucket
    if _bucket is None:
        _bucket = TokenBucket(settings.amap_qps)
    return _bucket


class AmapUnavailableError(Exception):
    pass


# 按交通方式隔离熔断：某一种方式持续失败时，不应把其余方式一起熔断。
_breakers: dict[str, CircuitBreaker] = {}


def _breaker(mode: str) -> CircuitBreaker:
    breaker = _breakers.get(mode)
    if breaker is None:
        breaker = CircuitBreaker(f"amap:{mode}")
        _breakers[mode] = breaker
    return breaker


def reset_breakers() -> None:
    """测试或运维手动恢复使用。"""
    _breakers.clear()
    global _bucket
    _bucket = None


def _to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _duration_minutes(value: Any) -> int | None:
    seconds = _to_int(value)
    if seconds <= 0:
        return None
    return max(1, round(seconds / 60))


def _parse_direction(payload: dict, mode: str) -> dict[str, Any] | None:
    if payload.get("status") != "1":
        raise AmapUnavailableError(f"amap error: {payload.get('info')}")

    route = payload.get("route") or {}
    if mode == "公交":
        transit = (route.get("transits") or [None])[0]
        if transit is None:
            return None
        cost = transit.get("cost") or {}
        # 高德 v5 的耗时在 cost.duration（秒），旧版在顶层 duration。
        minutes = _duration_minutes(cost.get("duration")) or _duration_minutes(
            transit.get("duration")
        )
        if minutes is None:
            return None
        return {
            "mode": mode,
            "duration_minutes": minutes,
            "distance_meters": _to_int(transit.get("distance")),
            "fare": cost.get("transit_fee"),
        }

    path = (route.get("paths") or [None])[0]
    if path is None:
        return None
    cost = path.get("cost") or {}
    minutes = _duration_minutes(cost.get("duration")) or _duration_minutes(
        path.get("duration")
    )
    if minutes is None:
        return None
    return {
        "mode": mode,
        "duration_minutes": minutes,
        "distance_meters": _to_int(path.get("distance")),
        "toll": cost.get("toll"),
    }


async def direction(origin: str, dest: str, mode: str) -> dict[str, Any] | None:
    if mode not in MODE_ENDPOINTS:
        raise ValueError(f"unsupported mode: {mode}")
    breaker = _breaker(mode)
    if not breaker.allow_request():
        raise AmapUnavailableError(f"amap {mode} 熔断中")
    params: dict[str, str] = {
        "key": settings.amap_web_key,
        "origin": origin,
        "destination": dest,
    }
    if mode == "公交":
        params["city1"] = CITY_ADCODE
        params["city2"] = CITY_ADCODE
        params["show_fields"] = "cost"
    else:
        params["show_fields"] = "cost"

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            await _get_bucket().acquire()
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{BASE_URL}{MODE_ENDPOINTS[mode]}", params=params
                )
                response.raise_for_status()
                payload = response.json()
            result = _parse_direction(payload, mode)
            breaker.record_success()
            if result is None:
                return {"mode": mode, "unavailable": True, "reason": "无可用路线方案"}
            result["source_channel"] = "高德"
            result["collected_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            return result
        except (httpx.HTTPError, AmapUnavailableError, ValueError, KeyError) as exc:
            last_error = exc
            logger.warning(
                "amap direction failed",
                extra={"kind": "external", "attempt": attempt, "mode": mode, "error": str(exc)[:200]},
            )
    breaker.record_failure()
    raise AmapUnavailableError(f"amap 请求失败: {last_error}")
