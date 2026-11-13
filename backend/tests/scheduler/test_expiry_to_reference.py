from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.scheduler.jobs.expiry_patrol as patrol_mod
import app.tools.pois as pois_mod
from app.core.config import settings
from app.services.itinerary_service import to_item_dto
from tests.api.test_chat import FakeSessionCtx
from tests.scheduler.test_expiry_patrol import (
    FakeExpiredHoursRepo,
    FakeExpiredPriceRepo,
    FakePatrolAuditRepo,
    FakePatrolEventRepo,
    FakePatrolItinRepo,
)


class FakePriceToolRepo:
    record = None

    def __init__(self, session) -> None:
        pass

    async def get_current(self, poi_id, price_type="门票"):
        if FakePriceToolRepo.record is not None and FakePriceToolRepo.record.poi_id == poi_id:
            return FakePriceToolRepo.record
        return None


def session_factory():
    return FakeSessionCtx()


@pytest.fixture()
def e2e_env(monkeypatch):
    FakeExpiredPriceRepo.reset()
    FakeExpiredHoursRepo.reset()
    FakePatrolEventRepo.reset()
    FakePatrolItinRepo.reset()
    FakePatrolAuditRepo.reset()
    FakePriceToolRepo.record = None

    monkeypatch.setattr(patrol_mod, "PriceRepository", FakeExpiredPriceRepo)
    monkeypatch.setattr(patrol_mod, "PoiHoursRepository", FakeExpiredHoursRepo)
    monkeypatch.setattr(patrol_mod, "ChangeEventRepository", FakePatrolEventRepo)
    monkeypatch.setattr(patrol_mod, "ItineraryRepository", FakePatrolItinRepo)
    monkeypatch.setattr(patrol_mod, "AuditLogRepository", FakePatrolAuditRepo)

    cache_sets: list = []

    async def fake_cache_get(key):
        return None

    async def fake_cache_set(key, value, ttl):
        cache_sets.append({"key": key, "value": value, "ttl": ttl})

    monkeypatch.setattr(pois_mod, "PriceRepository", FakePriceToolRepo)
    monkeypatch.setattr(pois_mod, "cache_get", fake_cache_get)
    monkeypatch.setattr(pois_mod, "cache_set", fake_cache_set)
    return SimpleNamespace(monkeypatch=monkeypatch, cache_sets=cache_sets)


def test_expiry_flow_marks_reference_end_to_end(e2e_env):
    FakeExpiredPriceRepo.records = [SimpleNamespace(poi_id=1, expired=True)]
    FakePriceToolRepo.record = SimpleNamespace(
        poi_id=1,
        amount=Decimal("60"),
        source_channel="官方渠道",
        collected_at=date(2026, 7, 1),
        valid_until=date.today() - timedelta(days=1),
    )

    stats = asyncio.run(patrol_mod.expiry_patrol(session_factory))
    assert stats["stale_prices"] == 1
    stale_events = [e for e in FakePatrolEventRepo.events if e.payload.get("kind") == "data_stale"]
    assert len(stale_events) == 1
    assert stale_events[0].entity_id == 1

    result = asyncio.run(pois_mod.get_ticket_price(FakeSessionCtx(), 1))

    assert result.ok is True
    info = result.data
    assert info.amount == 60.0
    assert info.freshness == "stale"
    assert info.is_reference is True
    assert e2e_env.cache_sets and e2e_env.cache_sets[0]["value"]["is_reference"] is True

    item = SimpleNamespace(
        seq=1,
        activity_type="sight",
        poi_id=1,
        start_at=None,
        end_at=None,
        transport_mode=None,
        transport_minutes=None,
        estimated_cost=60,
        cost_meta={
            "source_channel": "官方渠道",
            "collected_at": "2026-07-01",
            "is_reference": bool(info.is_reference),
        },
    )
    dto = to_item_dto(item)
    assert dto["estimated_cost"] == "60.00"
    assert dto["cost_meta"]["is_reference"] is True
    assert dto["cost_meta"]["source_channel"] == "官方渠道"


def test_fresh_price_stays_non_reference(e2e_env):
    FakePriceToolRepo.record = SimpleNamespace(
        poi_id=2,
        amount=Decimal("80"),
        source_channel="实地采集",
        collected_at=date.today() - timedelta(days=2),
        valid_until=date.today() + timedelta(days=30),
    )

    result = asyncio.run(pois_mod.get_ticket_price(FakeSessionCtx(), 2))

    assert result.ok is True
    assert result.data.freshness == "fresh"
    assert result.data.is_reference is False


def test_validity_defaults_are_configured():
    assert settings.data_validity_days_official == 90
    assert settings.data_validity_days_field == 30
    assert settings.data_validity_days_report == 14