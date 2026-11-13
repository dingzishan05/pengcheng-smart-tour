from __future__ import annotations

from datetime import date, timedelta

from app.cache.keys import weather_key
from app.cache.service import cache_get
from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.scheduler.jobs.shared import issue_weather_alert
from app.tools.live import get_weather

logger = get_logger("app.scheduler.weather_prefetch")

CITY = "徐州"
ALERT_LEVELS = ("暴雨", "大风")


async def weather_prefetch(session_factory=SessionFactory) -> dict:
    today = date.today()
    days = [today, today + timedelta(days=1)]
    prefetched: list[str] = []
    for visit_date in days:
        result = await get_weather(CITY, visit_date)
        if result.ok:
            prefetched.append(visit_date.isoformat())

    alerts: list[dict] = []
    for visit_date in days:
        snapshot = await cache_get(weather_key(CITY, visit_date.isoformat()))
        if snapshot is None:
            continue
        level = snapshot.get("alert_level")
        if level in ALERT_LEVELS:
            alerts.append({"date": visit_date.isoformat(), "alert_level": level})
            await issue_weather_alert(session_factory, visit_date, level)

    stats = {"prefetched": prefetched, "alerts": alerts}
    logger.info("weather_prefetch done", extra={"kind": "system", "days": prefetched, "alerts": len(alerts)})
    return stats