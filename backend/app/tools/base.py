from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from app.cache.service import cache_get, cache_set

T = TypeVar("T", bound=BaseModel)


class ToolErrorKind(str, Enum):
    DATA = "data"
    ALGORITHM = "algorithm"
    EXTERNAL = "external"


class ToolError(BaseModel):
    kind: ToolErrorKind
    code: str
    message: str
    retried: bool = False


class ToolMeta(BaseModel):
    source_channel: str = ""
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    degraded: bool = False


class ToolResult(BaseModel, Generic[T]):
    ok: bool
    data: T | None = None
    error: ToolError | None = None
    meta: ToolMeta = Field(default_factory=ToolMeta)

    @classmethod
    def success(cls, data: T, meta: ToolMeta | None = None) -> "ToolResult[T]":
        return cls(ok=True, data=data, meta=meta or ToolMeta())

    @classmethod
    def failure(
        cls,
        kind: ToolErrorKind,
        code: str,
        message: str,
        retried: bool = False,
        meta: ToolMeta | None = None,
        data: T | None = None,
    ) -> "ToolResult[T]":
        return cls(
            ok=False,
            data=data,
            error=ToolError(kind=kind, code=code, message=message, retried=retried),
            meta=meta or ToolMeta(degraded=data is not None),
        )


def with_retry(times: int = 1, backoff: float = 0.5) -> Callable:
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any):
            last_error: Exception | None = None
            for attempt in range(times + 1):
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    last_error = exc
                    if attempt < times:
                        await asyncio.sleep(backoff * (attempt + 1))
            assert last_error is not None
            raise last_error

        return wrapper

    return decorator


def cached(key: str | Callable[..., str], ttl: int) -> Callable:
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any):
            cache_key = key(*args, **kwargs) if callable(key) else key
            hit = await cache_get(cache_key)
            if hit is not None:
                return hit
            result = await fn(*args, **kwargs)
            await cache_set(cache_key, result, ttl)
            return result

        return wrapper

    return decorator


async def get_stale(key: str) -> Any | None:
    return await cache_get(key)