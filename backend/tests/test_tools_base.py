from __future__ import annotations

from unittest.mock import patch

from pydantic import BaseModel

from app.tools.base import ToolErrorKind, ToolResult, cached, with_retry


class Payload(BaseModel):
    value: int


async def test_with_retry_retries_then_raises():
    calls = {"n": 0}

    @with_retry(times=2, backoff=0)
    async def flaky():
        calls["n"] += 1
        raise RuntimeError("boom")

    try:
        await flaky()
    except RuntimeError:
        pass
    assert calls["n"] == 3


async def test_with_retry_succeeds_second_attempt():
    calls = {"n": 0}

    @with_retry(times=1, backoff=0)
    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return Payload(value=42)

    result = await flaky()
    assert result.value == 42
    assert calls["n"] == 2


async def test_cached_hit_skips_retry_and_function():
    calls = {"fn": 0}

    @cached("unit:key", ttl=60)
    @with_retry(times=1, backoff=0)
    async def load():
        calls["fn"] += 1
        return Payload(value=1)

    with patch("app.tools.base.cache_get", return_value={"value": 9}):
        result = await load()
    assert result["value"] == 9
    assert calls["fn"] == 0


async def test_cached_miss_executes_and_sets():
    calls = {"fn": 0}
    set_keys: list[tuple[str, int]] = []

    @cached("unit:miss", ttl=30)
    async def load():
        calls["fn"] += 1
        return Payload(value=5)

    async def fake_set(key, value, ttl):
        set_keys.append((key, ttl))

    with (
        patch("app.tools.base.cache_get", return_value=None),
        patch("app.tools.base.cache_set", side_effect=fake_set),
    ):
        result = await load()
    assert result.value == 5
    assert calls["fn"] == 1
    assert set_keys == [("unit:miss", 30)]


async def test_cached_key_callable():
    @cached(lambda a, b: f"k:{a}-{b}", ttl=10)
    async def load(a, b):
        return Payload(value=a + b)

    seen: list[str] = []

    async def fake_get(key):
        seen.append(key)
        return None

    async def fake_set(key, value, ttl):
        pass

    with (
        patch("app.tools.base.cache_get", side_effect=fake_get),
        patch("app.tools.base.cache_set", side_effect=fake_set),
    ):
        result = await load(1, 2)
    assert result.value == 3
    assert seen == ["k:1-2"]


def test_tool_result_success_and_failure():
    ok = ToolResult.success(Payload(value=1))
    assert ok.ok and ok.data.value == 1 and ok.error is None

    failure = ToolResult.failure(ToolErrorKind.EXTERNAL, "50001", "服务失败", retried=True)
    assert not failure.ok
    assert failure.error.kind == ToolErrorKind.EXTERNAL
    assert failure.error.retried is True
    assert failure.meta.degraded is False