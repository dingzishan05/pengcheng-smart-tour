import redis.asyncio as aioredis

from app.core.config import settings

redis_client: aioredis.Redis = aioredis.from_url(
    settings.redis_url,
    socket_timeout=settings.redis_timeout_ms / 1000,
    socket_connect_timeout=settings.redis_timeout_ms / 1000,
    decode_responses=True,
)


async def ping_redis() -> bool:
    try:
        return bool(await redis_client.ping())
    except Exception:
        return False


async def close_redis() -> None:
    await redis_client.aclose()