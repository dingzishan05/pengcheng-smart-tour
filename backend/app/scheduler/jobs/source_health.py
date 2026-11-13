from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.cache.keys import sources_status_key
from app.cache.service import cache_set
from app.clients.weather import WeatherUnavailableError, fetch_weather
from app.core.config import settings
from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.models import DataSource
from app.repositories.knowledge_repo import DataSourceRepository
from app.cache.redis_client import ping_redis
from app.core.db import engine
from sqlalchemy import text

logger = get_logger("app.scheduler.source_health")

AMAP_PROBE_URL = "https://restapi.amap.com/v3/ip"
SOURCES_TTL_SECONDS = 600

PROBE_NAMES = ("高德路线", "天气服务", "LLM 服务", "数据库", "Redis")
KIND_BY_NAME = {
    "高德路线": "external",
    "天气服务": "external",
    "LLM 服务": "external",
    "数据库": "internal",
    "Redis": "internal",
}


async def probe_amap() -> tuple[bool, str | None]:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(AMAP_PROBE_URL, params={"key": settings.amap_web_key})
        if response.status_code >= 500:
            return False, f"amap http {response.status_code}"
        return True, None
    except httpx.HTTPError as exc:
        return False, str(exc)[:200]


async def probe_weather() -> tuple[bool, str | None]:
    try:
        await fetch_weather()
        return True, None
    except WeatherUnavailableError as exc:
        return False, str(exc)[:200]


async def probe_llm() -> tuple[bool, str | None]:
    if not settings.llm_base_url:
        return False, "llm_base_url 未配置"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(settings.llm_base_url)
        if response.status_code >= 500:
            return False, f"llm http {response.status_code}"
        return True, None
    except httpx.HTTPError as exc:
        return False, str(exc)[:200]


async def probe_database() -> tuple[bool, str | None]:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:
        return False, str(exc)[:200]


async def probe_redis() -> tuple[bool, str | None]:
    ok = await ping_redis()
    return (True, None) if ok else (False, "redis ping failed")


async def source_health_check(session_factory=SessionFactory) -> dict:
    probes = {
        "高德路线": probe_amap,
        "天气服务": probe_weather,
        "LLM 服务": probe_llm,
        "数据库": probe_database,
        "Redis": probe_redis,
    }
    results: dict[str, tuple[bool, str | None]] = {}
    for name, probe in probes.items():
        try:
            results[name] = await probe()
        except Exception as exc:
            results[name] = (False, str(exc)[:200])

    now = datetime.now(timezone.utc)
    snapshot: list[dict] = []
    async with session_factory() as session:
        repo = DataSourceRepository(session)
        for name in PROBE_NAMES:
            ok, error = results[name]
            source = await repo.get_by_name(name)
            if source is None:
                source = DataSource(
                    name=name,
                    kind=KIND_BY_NAME[name],
                    availability="可用",
                )
            source.availability = "可用" if ok else "不可用"
            source.last_error = None if ok else error
            if ok:
                source.last_success_at = now
            if source.id is None:
                await repo.add(source)
            snapshot.append(
                {
                    "name": name,
                    "kind": KIND_BY_NAME[name],
                    "availability": source.availability,
                    "last_success_at": source.last_success_at.isoformat()
                    if source.last_success_at
                    else None,
                    "last_error": source.last_error,
                }
            )
        await session.commit()
    await cache_set(sources_status_key(), {"sources": snapshot}, SOURCES_TTL_SECONDS)
    stats = {
        "checked": len(PROBE_NAMES),
        "available": sum(1 for ok, _ in results.values() if ok),
    }
    logger.info("source_health_check done", extra={"kind": "system", **stats})
    return stats