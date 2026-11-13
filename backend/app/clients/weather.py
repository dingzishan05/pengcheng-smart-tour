import time
from typing import Any

import httpx

from app.clients.circuit_breaker import weather_breaker
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("app.weather")

BASE_URL = "https://restapi.amap.com/v3/weather/weatherInfo"
XUZHOU_ADCODE = "320300"

ALERT_KEYWORDS = ("暴雨", "大风", "台风", "暴雪", "冰雹")


class WeatherUnavailableError(Exception):
    pass


def _parse_lives(payload: dict) -> dict[str, Any] | None:
    if payload.get("status") != "1":
        raise WeatherUnavailableError(f"weather error: {payload.get('info')}")
    lives = payload.get("lives") or []
    if not lives:
        return None
    live = lives[0]
    weather_text = live.get("weather", "")
    alert_level = next((kw for kw in ALERT_KEYWORDS if kw in weather_text), None)
    return {
        "condition": weather_text,
        "temperature": live.get("temperature"),
        "precipitation_prob": None,
        "alert_level": alert_level,
        "collected_at": live.get("reporttime") or time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


async def fetch_weather(city_adcode: str = XUZHOU_ADCODE) -> dict[str, Any]:
    if not weather_breaker.allow_request():
        raise WeatherUnavailableError("weather 熔断中")
    params = {"key": settings.amap_web_key, "city": city_adcode, "extensions": "base"}
    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(BASE_URL, params=params)
                response.raise_for_status()
                payload = response.json()
            result = _parse_lives(payload)
            weather_breaker.record_success()
            if result is None:
                raise WeatherUnavailableError("weather 返回为空")
            result["degraded"] = False
            return result
        except (httpx.HTTPError, WeatherUnavailableError) as exc:
            last_error = exc
            logger.warning(
                "weather fetch failed",
                extra={"kind": "external", "attempt": attempt, "error": str(exc)[:200]},
            )
    weather_breaker.record_failure()
    raise WeatherUnavailableError(f"weather 请求失败: {last_error}")