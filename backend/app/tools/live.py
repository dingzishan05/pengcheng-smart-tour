from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timezone

from pydantic import BaseModel, Field

from app.cache.keys import route_key, weather_key
from app.cache.service import cache_get, cache_set
from app.clients.amap import AmapUnavailableError, direction
from app.clients.weather import WeatherUnavailableError, fetch_weather
from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import record_cache_hit, record_cache_miss
from app.tools.base import ToolErrorKind, ToolMeta, ToolResult
from app.tools.pois import Coordinate

logger = get_logger("app.tools.live")

ROUTE_TTL = settings.route_cache_ttl
WEATHER_TTL = settings.weather_cache_ttl


class RouteOptionInfo(BaseModel):
    mode: str
    duration_minutes: int | None = None
    distance_meters: int | None = None
    unavailable: bool = False
    reason: str | None = None
    collected_at: datetime | None = None
    degraded: bool = False


class WeatherSnapshot(BaseModel):
    condition: str = ""
    temperature: str | None = None
    precipitation_prob: str | None = None
    alert_level: str | None = None
    collected_at: datetime | None = None
    degraded: bool = False


def _geo(point: Coordinate) -> str:
    return f"{point.lng:.6f},{point.lat:.6f}"


async def get_route(origin: Coordinate, destination: Coordinate, mode: str) -> ToolResult[RouteOptionInfo]:
    key = route_key(_geo(origin), _geo(destination), mode)
    collected_at = datetime.now(timezone.utc)
    try:
        result = await direction(_geo(origin), _geo(destination), mode)
    except AmapUnavailableError as exc:
        stale = await cache_get(f"{key}:stale")
        if stale is not None:
            info = RouteOptionInfo.model_validate(stale)
            info.degraded = True
            return ToolResult.success(info, ToolMeta(source_channel="redis_stale", collected_at=info.collected_at, degraded=True))
        return ToolResult.success(
            RouteOptionInfo(mode=mode, unavailable=True, reason=f"暂无{mode}数据"),
            ToolMeta(source_channel="amap", collected_at=collected_at),
        )

    if result.get("unavailable"):
        return ToolResult.success(
            RouteOptionInfo(mode=mode, unavailable=True, reason=result.get("reason", "暂无数据")),
            ToolMeta(source_channel="amap", collected_at=collected_at),
        )

    info = RouteOptionInfo(
        mode=mode,
        duration_minutes=result.get("duration_minutes"),
        distance_meters=result.get("distance_meters"),
        collected_at=collected_at,
    )
    await cache_set(key, info.model_dump(), ROUTE_TTL)
    await cache_set(f"{key}:stale", info.model_dump(), ROUTE_TTL * 3)
    return ToolResult.success(info, ToolMeta(source_channel="amap", collected_at=collected_at))


async def get_weather(city: str, visit_date: date) -> ToolResult[WeatherSnapshot]:
    key = weather_key(city, visit_date.isoformat())
    hit = await cache_get(key)
    if hit is not None:
        record_cache_hit()
        return ToolResult.success(WeatherSnapshot.model_validate(hit))
    record_cache_miss()

    try:
        payload = await fetch_weather()
        snapshot = WeatherSnapshot(
            condition=payload.get("condition", ""),
            temperature=payload.get("temperature"),
            precipitation_prob=payload.get("precipitation_prob"),
            alert_level=payload.get("alert_level"),
            collected_at=datetime.now(timezone.utc),
        )
        await cache_set(key, snapshot.model_dump(), WEATHER_TTL)
        return ToolResult.success(snapshot, ToolMeta(source_channel="amap_weather"))
    except WeatherUnavailableError:
        stale = await cache_get(f"{key}:stale")
        if stale is not None:
            snapshot = WeatherSnapshot.model_validate(stale)
            snapshot.degraded = True
            return ToolResult.success(snapshot, ToolMeta(source_channel="redis_stale", degraded=True))
        return ToolResult.failure(
            ToolErrorKind.EXTERNAL, "50004", "天气服务暂不可用，请稍后重试"
        )
    except Exception as exc:
        logger.error("get_weather failed", extra={"kind": "external", "error": str(exc)[:200]})
        return ToolResult.failure(ToolErrorKind.EXTERNAL, "50004", "天气查询失败")