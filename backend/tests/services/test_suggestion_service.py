from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

from app.core.errors import BizError, ErrorCode
from app.services.replan_service import ReplanChanges
from app.services.suggestion_service import SuggestionService
from tests.api.test_chat import FakeSessionCtx


class FakeSuggestionItinRepo:
    itineraries: list = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.itineraries = []

    async def list_affected(self, itinerary_ids):
        return [
            i
            for i in self.itineraries
            if i.id in itinerary_ids and i.status in ("已确认", "草稿", "挂起建议")
        ]

    async def list_confirmed(self):
        return [i for i in self.itineraries if i.status == "已确认"]


class FakeInnerReplanService:
    calls: list = []
    fail_for: set = set()

    async def refresh(self, session_factory, conversation_id, plan_id, trigger, changes):
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "plan_id": plan_id,
                "trigger": trigger,
                "changes": changes,
            }
        )
        if plan_id in self.fail_for:
            raise BizError(ErrorCode.STATE_CONFLICT, "状态冲突")
        return {
            "new_plan": {"plan_id": str(plan_id)},
            "diff": {"cost_delta": "20.00"},
            "conflict": None,
        }


def make_itinerary(itin_id, status="已确认", visit_date="2026-10-21", conversation_id=9):
    return SimpleNamespace(
        id=itin_id,
        conversation_id=conversation_id,
        status=status,
        request_snapshot={"visit_date": visit_date},
    )


@pytest.fixture()
def suggestion_env(monkeypatch):
    FakeSuggestionItinRepo.reset()
    FakeInnerReplanService.calls = []
    FakeInnerReplanService.fail_for = set()
    import app.services.suggestion_service as mod

    monkeypatch.setattr(mod, "ItineraryRepository", FakeSuggestionItinRepo)
    return SimpleNamespace(monkeypatch=monkeypatch)


def make_service():
    return SuggestionService(replan_service=FakeInnerReplanService())


def session_factory():
    return FakeSessionCtx()


def test_price_change_suggests_for_confirmed_only(suggestion_env):
    FakeSuggestionItinRepo.itineraries = [
        make_itinerary(1, status="已确认"),
        make_itinerary(2, status="草稿"),
    ]

    results = asyncio.run(
        make_service().on_price_change(session_factory, 3, 80.0, [1, 2])
    )

    assert len(results) == 1
    assert results[0]["plan_id"] == "1"
    assert results[0]["created"] is True
    assert results[0]["cost_delta"] == "20.00"
    assert len(FakeInnerReplanService.calls) == 1
    call = FakeInnerReplanService.calls[0]
    assert call["trigger"] == "data_change"
    assert call["changes"].price_hike == {"poi_id": 3, "new_price": 80.0}


def test_price_change_bizerror_isolated(suggestion_env):
    FakeSuggestionItinRepo.itineraries = [make_itinerary(1), make_itinerary(2)]
    FakeInnerReplanService.fail_for = {1}

    results = asyncio.run(
        make_service().on_price_change(session_factory, 3, 80.0, [1, 2])
    )

    assert len(results) == 2
    assert results[0]["created"] is False
    assert results[0]["reason"] == "状态冲突"
    assert results[1]["created"] is True


def test_price_change_empty_affected(suggestion_env):
    results = asyncio.run(make_service().on_price_change(session_factory, 3, 80.0, []))
    assert results == []
    assert FakeInnerReplanService.calls == []


def test_poi_closed_builds_poi_closed_changes(suggestion_env):
    FakeSuggestionItinRepo.itineraries = [make_itinerary(1)]

    results = asyncio.run(make_service().on_poi_closed(session_factory, 7, [1]))

    assert results[0]["created"] is True
    changes = FakeInnerReplanService.calls[0]["changes"]
    assert isinstance(changes, ReplanChanges)
    assert changes.poi_closed == {"poi_id": 7}


def test_weather_alert_hits_matching_date_only(suggestion_env):
    FakeSuggestionItinRepo.itineraries = [
        make_itinerary(1, visit_date="2026-10-21"),
        make_itinerary(2, visit_date="2026-11-01"),
        make_itinerary(3, visit_date="2026-10-21"),
    ]

    results = asyncio.run(
        make_service().on_weather_alert(session_factory, date(2026, 10, 21), "暴雨")
    )

    assert len(results) == 2
    assert {r["plan_id"] for r in results} == {"1", "3"}
    changes = FakeInnerReplanService.calls[0]["changes"]
    assert changes.heavy_rain is True