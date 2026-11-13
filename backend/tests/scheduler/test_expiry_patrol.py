from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

import app.scheduler.jobs.expiry_patrol as patrol_mod
from tests.api.test_chat import FakeSessionCtx


class FakeExpiredPriceRepo:
    records: list = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.records = []

    async def list_expired_current(self, today):
        return [r for r in self.records if r.expired]


class FakeExpiredHoursRepo:
    rows: list = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.rows = []

    async def list_expired(self, today):
        return [h for h in self.rows if h.expired]


class FakePatrolEventRepo:
    events: list = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.events = []

    async def append(self, event_type, entity_type, entity_id, payload, affected_itinerary_ids=None):
        event = SimpleNamespace(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            affected_itinerary_ids=affected_itinerary_ids or [],
            occurred_at=datetime.now(),
        )
        self.events.append(event)
        return event

    async def list_by_entity(self, entity_type, entity_id, since=None):
        rows = [e for e in self.events if e.entity_type == entity_type and e.entity_id == entity_id]
        if since is not None:
            rows = [e for e in rows if e.occurred_at >= since]
        return rows


class FakePatrolItinRepo:
    itineraries: list = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.itineraries = []

    async def list_referencing(self, poi_id):
        return [
            SimpleNamespace(id=i["id"])
            for i in self.itineraries
            if i["poi_id"] == poi_id
        ]


class FakePatrolAuditRepo:
    logs: list = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.logs = []

    async def append(self, operator_id, action, entity_type, entity_id, before_value=None, after_value=None):
        log = SimpleNamespace(
            operator_id=operator_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before_value=before_value,
            after_value=after_value,
        )
        self.logs.append(log)
        return log


@pytest.fixture()
def patrol_env(monkeypatch):
    FakeExpiredPriceRepo.reset()
    FakeExpiredHoursRepo.reset()
    FakePatrolEventRepo.reset()
    FakePatrolItinRepo.reset()
    FakePatrolAuditRepo.reset()
    monkeypatch.setattr(patrol_mod, "PriceRepository", FakeExpiredPriceRepo)
    monkeypatch.setattr(patrol_mod, "PoiHoursRepository", FakeExpiredHoursRepo)
    monkeypatch.setattr(patrol_mod, "ChangeEventRepository", FakePatrolEventRepo)
    monkeypatch.setattr(patrol_mod, "ItineraryRepository", FakePatrolItinRepo)
    monkeypatch.setattr(patrol_mod, "AuditLogRepository", FakePatrolAuditRepo)
    yield monkeypatch


def session_factory():
    return FakeSessionCtx()


def test_expiry_patrol_creates_stale_events(patrol_env):
    FakeExpiredPriceRepo.records = [SimpleNamespace(poi_id=1, expired=True)]
    FakeExpiredHoursRepo.rows = [SimpleNamespace(poi_id=2, expired=True)]
    FakePatrolItinRepo.itineraries = [{"id": 5, "poi_id": 1}, {"id": 6, "poi_id": 1}]

    stats = __import__("asyncio").run(patrol_mod.expiry_patrol(session_factory))

    assert stats["stale_prices"] == 1
    assert stats["stale_hours"] == 1
    assert stats["events_created"] == 2
    assert stats["skipped"] == 0
    assert len(FakePatrolEventRepo.events) == 2
    price_event = next(e for e in FakePatrolEventRepo.events if e.entity_id == 1)
    assert price_event.event_type == "价格变更"
    assert price_event.payload["kind"] == "data_stale"
    assert price_event.affected_itinerary_ids == [5, 6]
    hours_event = next(e for e in FakePatrolEventRepo.events if e.entity_id == 2)
    assert hours_event.event_type == "POI状态变更"
    assert any(l.action == "expiry_patrol" for l in FakePatrolAuditRepo.logs)


def test_expiry_patrol_idempotent_on_rerun(patrol_env):
    FakeExpiredPriceRepo.records = [SimpleNamespace(poi_id=1, expired=True)]
    factory = session_factory

    first = __import__("asyncio").run(patrol_mod.expiry_patrol(factory))
    second = __import__("asyncio").run(patrol_mod.expiry_patrol(factory))

    assert first["events_created"] == 1
    assert second["events_created"] == 0
    assert second["skipped"] == 1
    assert len(FakePatrolEventRepo.events) == 1


def test_expiry_patrol_no_expired_data(patrol_env):
    FakeExpiredPriceRepo.records = [SimpleNamespace(poi_id=1, expired=False)]
    FakeExpiredHoursRepo.rows = []

    stats = __import__("asyncio").run(patrol_mod.expiry_patrol(session_factory))

    assert stats["stale_prices"] == 0
    assert stats["stale_hours"] == 0
    assert stats["events_created"] == 0
    assert FakePatrolEventRepo.events == []