from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import app.scheduler.jobs.weather_prefetch as prefetch_mod
from app.tools.base import ToolErrorKind, ToolMeta, ToolResult
from app.tools.live import WeatherSnapshot
from tests.api.test_chat import FakeSessionCtx


@pytest.fixture()
def prefetch_env(monkeypatch):
    issued: list = []
    get_weather_calls: list = []

    async def fake_get_weather(city, visit_date):
        get_weather_calls.append((city, visit_date))
        return ToolResult.success(
            WeatherSnapshot(condition="晴", temperature="20"),
            ToolMeta(source_channel="amap_weather"),
        )

    async def fake_issue(session_factory, visit_date, level):
        issued.append((visit_date, level))
        return {"event": True}

    monkeypatch.setattr(prefetch_mod, "get_weather", fake_get_weather)
    monkeypatch.setattr(prefetch_mod, "issue_weather_alert", fake_issue)
    return SimpleNamespace(
        monkeypatch=monkeypatch,
        issued=issued,
        get_weather_calls=get_weather_calls,
    )



def session_factory():
    return FakeSessionCtx()


def test_prefetch_without_alert(prefetch_env):
    async def fake_cache_get(key):
        return {"condition": "晴", "alert_level": None}

    prefetch_env.monkeypatch.setattr(prefetch_mod, "cache_get", fake_cache_get)

    stats = asyncio.run(prefetch_mod.weather_prefetch(session_factory))

    today = date.today()
    tomorrow = today + timedelta(days=1)
    assert prefetch_env.get_weather_calls == [("徐州", today), ("徐州", tomorrow)]
    assert prefetch_env.issued == []
    assert stats["alerts"] == []


def test_prefetch_with_heavy_rain_alert(prefetch_env):
    today = date.today()

    async def fake_cache_get(key):
        if today.isoformat() in key:
            return {"condition": "暴雨", "alert_level": "暴雨"}
        return {"condition": "晴", "alert_level": None}

    prefetch_env.monkeypatch.setattr(prefetch_mod, "cache_get", fake_cache_get)

    stats = asyncio.run(prefetch_mod.weather_prefetch(session_factory))

    assert prefetch_env.issued == [(today, "暴雨")]
    assert stats["alerts"] == [{"date": today.isoformat(), "alert_level": "暴雨"}]


def test_prefetch_tolerates_fetch_failure(prefetch_env):
    async def failing_get_weather(city, visit_date):
        return ToolResult.failure(
            ToolErrorKind.EXTERNAL,
            "WEATHER_DOWN",
            "天气服务不可用",
        )

    async def fake_cache_get(key):
        return None

    prefetch_env.monkeypatch.setattr(prefetch_mod, "get_weather", failing_get_weather)
    prefetch_env.monkeypatch.setattr(prefetch_mod, "cache_get", fake_cache_get)

    stats = asyncio.run(prefetch_mod.weather_prefetch(session_factory))

    assert stats["prefetched"] == []
    assert stats["alerts"] == []