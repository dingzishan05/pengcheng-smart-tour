from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import app.scheduler.jobs.source_health as health_mod
from tests.api.test_chat import FakeSessionCtx


class FakeHealthSourceRepo:
    sources: dict = {}

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.sources = {}

    async def get_by_name(self, name):
        return FakeHealthSourceRepo.sources.get(name)

    async def add(self, source):
        source.id = len(FakeHealthSourceRepo.sources) + 1
        FakeHealthSourceRepo.sources[source.name] = source
        return source


@pytest.fixture()
def health_env(monkeypatch):
    FakeHealthSourceRepo.reset()
    snapshots: list = []

    async def fake_cache_set(key, value, ttl):
        snapshots.append({"key": key, "value": value, "ttl": ttl})

    async def ok():
        return (True, None)

    async def bad():
        return (False, "timeout after 3s")

    monkeypatch.setattr(health_mod, "DataSourceRepository", FakeHealthSourceRepo)
    monkeypatch.setattr(health_mod, "cache_set", fake_cache_set)
    monkeypatch.setattr(health_mod, "probe_amap", ok)
    monkeypatch.setattr(health_mod, "probe_weather", ok)
    monkeypatch.setattr(health_mod, "probe_llm", ok)
    monkeypatch.setattr(health_mod, "probe_database", ok)
    monkeypatch.setattr(health_mod, "probe_redis", ok)
    yield SimpleNamespace(monkeypatch=monkeypatch, snapshots=snapshots, ok=ok, bad=bad)


def session_factory():
    return FakeSessionCtx()


def test_source_health_all_available(health_env):
    stats = __import__("asyncio").run(health_mod.source_health_check(session_factory))

    assert stats == {"checked": 5, "available": 5}
    assert set(FakeHealthSourceRepo.sources) == set(health_mod.PROBE_NAMES)
    for source in FakeHealthSourceRepo.sources.values():
        assert source.availability == "可用"
        assert source.last_success_at is not None
        assert source.last_error is None
    assert len(health_env.snapshots) == 1
    snapshot = health_env.snapshots[0]
    assert snapshot["key"] == "sources:status"
    assert len(snapshot["value"]["sources"]) == 5
    assert all(s["availability"] == "可用" for s in snapshot["value"]["sources"])


def test_source_health_marks_unavailable(health_env):
    health_env.monkeypatch.setattr(health_mod, "probe_weather", health_env.bad)

    stats = __import__("asyncio").run(health_mod.source_health_check(session_factory))

    assert stats["available"] == 4
    weather = FakeHealthSourceRepo.sources["天气服务"]
    assert weather.availability == "不可用"
    assert weather.last_error == "timeout after 3s"
    assert weather.last_success_at is None
    snapshot_weather = next(
        s for s in health_env.snapshots[0]["value"]["sources"] if s["name"] == "天气服务"
    )
    assert snapshot_weather["availability"] == "不可用"


def test_source_health_updates_existing_source(health_env):
    FakeHealthSourceRepo.sources["高德路线"] = SimpleNamespace(
        id=1,
        name="高德路线",
        kind="external",
        availability="不可用",
        last_success_at=None,
        last_error="old error",
    )

    __import__("asyncio").run(health_mod.source_health_check(session_factory))

    amap = FakeHealthSourceRepo.sources["高德路线"]
    assert amap.availability == "可用"
    assert amap.last_error is None
    assert amap.last_success_at is not None
    assert len(FakeHealthSourceRepo.sources) == 5