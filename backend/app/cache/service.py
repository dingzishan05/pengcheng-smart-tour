import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.cache import keys
from app.cache.redis_client import redis_client
from app.core.logging import get_logger
from app.core.metrics import record_cache_hit, record_cache_miss

logger = get_logger("app.cache")


async def cache_get(key: str) -> Any | None:
    try:
        raw = await redis_client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None


async def cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    try:
        await redis_client.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl_seconds)
    except Exception:
        logger.warning("cache set failed", extra={"kind": "system", "key": key})


async def cache_delete(key: str) -> None:
    try:
        await redis_client.delete(key)
    except Exception:
        logger.warning("cache delete failed", extra={"kind": "system", "key": key})


async def invalidate_with_retry(key: str, retries: int = 3) -> None:
    for attempt in range(1, retries + 1):
        try:
            await redis_client.delete(key)
            return
        except Exception:
            logger.warning(
                "cache invalidate retry",
                extra={"kind": "system", "key": key, "attempt": attempt},
            )


async def get_or_load(
    key: str, ttl_seconds: int, loader: Callable[[], Awaitable[Any]]
) -> dict[str, Any]:
    cached = await cache_get(key)
    if cached is not None:
        record_cache_hit()
        return {"hit": True, "value": cached}
    record_cache_miss()
    value = await loader()
    if value is not None:
        await cache_set(key, value, ttl_seconds)
    return {"hit": False, "value": value}


async def read_through(
    key: str,
    ttl_seconds: int,
    loader: Callable[[], Awaitable[Any]],
    fallback_to_stale: bool = True,
) -> dict[str, Any]:
    result = await get_or_load(key, ttl_seconds, loader)
    if result["value"] is not None:
        return result
    if fallback_to_stale:
        stale = await _get_no_refresh(key)
        if stale is not None:
            return {"hit": False, "value": stale, "degraded": True}
    return {"hit": False, "value": None, "degraded": False}


async def _get_no_refresh(key: str) -> Any | None:
    try:
        raw = await redis_client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None


async def invalidate_poi_cache() -> None:
    await invalidate_with_retry(keys.poi_active_key())


async def invalidate_price_cache(poi_id: int) -> None:
    await invalidate_with_retry(keys.price_key(poi_id))


async def invalidate_sources_cache() -> None:
    await invalidate_with_retry(keys.sources_status_key())


async def invalidate_embedding_caches() -> None:
    try:
        emb_keys = [k async for k in redis_client.scan_iter(match="emb:*", count=100)]
        if emb_keys:
            await redis_client.delete(*emb_keys)
    except Exception:
        logger.warning("embedding cache invalidation failed", extra={"kind": "system"})