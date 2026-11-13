from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

import app.scheduler.jobs.route_warmup as warmup_mod
from app.tools.base import ToolMeta, ToolResult
from app.tools.live import RouteOptionInfo
from tests.api.test_chat import FakeSessionCtx


def make_poi(poi_id, lng, lat):
    return SimpleNamespace(id=poi_id, longitude=lng, latitude=lat, status="有效")


class FakeWarmupPoiRepo:
    pois: list = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.pois = []

    async def list_active(self):
        return list(self.pois)


class FakeRouteStoreRepo:
    routes: list = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.routes = []

    async def upsert_route(self, route):
        self.routes.append(route)
        return route


@pytest.fixture()
def warmup_env(monkeypatch):
    FakeWarmupPoiRepo.reset()
    FakeRouteStoreRepo.reset()
    calls: list = []

    async def fake_get_route(origin, destination, mode):
        calls.append((origin.lng, origin.lat, destination.lng, destination.lat, mode))
        if mode == "步行":
            return ToolResult.success(
                RouteOptionInfo(mode=mode, unavailable=True, reason="暂无步行数据"),
                ToolMeta(source_channel="amap"),
            )
        return ToolResult.success(
            RouteOptionInfo(
                mode=mode,
                duration_minutes=20,
                distance_meters=3000,
                collected_at=None,
            ),
            ToolMeta(source_channel="amap"),
        )

    monkeypatch.setattr(warmup_mod, "PoiRepository", FakeWarmupPoiRepo)
    monkeypatch.setattr(warmup_mod, "TransportRouteRepository", FakeRouteStoreRepo)
    monkeypatch.setattr(warmup_mod, "get_route", fake_get_route)
    return SimpleNamespace(calls=calls)


def session_factory():
    return FakeSessionCtx()


def test_route_warmup_builds_matrix(warmup_env):
    FakeWarmupPoiRepo.pois = [
        make_poi(1, 117.15, 34.29),
        make_poi(2, 117.19, 34.26),
        make_poi(3, 117.18, 34.21),
    ]

    stats = asyncio.run(warmup_mod.route_matrix_warmup(session_factory))

    assert stats["pairs"] == 3 * 2 * 3
    assert stats["saved"] == 3 * 2 * 2
    assert stats["unavailable"] == 3 * 2
    assert len(warmup_env.calls) == 18
    assert all(call[4] in ("公交", "步行", "驾车") for call in warmup_env.calls)
    assert len(FakeRouteStoreRepo.routes) == 12
    saved_route = FakeRouteStoreRepo.routes[0]
    assert saved_route.source_channel == "高德"
    assert saved_route.duration_minutes == 20
    assert saved_route.collected_at is not None


def test_route_warmup_idempotent_rerun(warmup_env):
    FakeWarmupPoiRepo.pois = [make_poi(1, 117.15, 34.29), make_poi(2, 117.19, 34.26)]
    factory = session_factory

    first = asyncio.run(warmup_mod.route_matrix_warmup(factory))
    FakeRouteStoreRepo.reset()
    second = asyncio.run(warmup_mod.route_matrix_warmup(factory))

    assert first["pairs"] == second["pairs"] == 2 * 1 * 3
    assert second["saved"] == 4
    assert len(FakeRouteStoreRepo.routes) == 4


def test_route_warmup_skips_when_few_pois(warmup_env):
    FakeWarmupPoiRepo.pois = [make_poi(1, 117.15, 34.29)]

    stats = asyncio.run(warmup_mod.route_matrix_warmup(session_factory))

    assert stats == {"pairs": 0, "saved": 0, "unavailable": 0, "skipped": True}
    assert warmup_env.calls == []