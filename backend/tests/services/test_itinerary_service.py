from __future__ import annotations

import pytest

from app.core.errors import BizError, ErrorCode
from app.models import Itinerary, ItineraryItem
from app.optimizer.models import Goal, ItineraryItem as PlanItem, ItineraryPlan, TransportMode, TransportSegment
from app.services.itinerary_service import (
    CONFIRMED,
    DRAFT,
    PENDING,
    REPLACED,
    ItineraryService,
    money,
    plan_to_items,
    items_to_plan,
    to_plan_detail,
)


class FakeItineraryRepo:
    def __init__(self) -> None:
        self.data: list[Itinerary] = []
        self._next_id = 100

    async def add(self, itin: Itinerary) -> Itinerary:
        itin.id = self._next_id
        self._next_id += 1
        self.data.append(itin)
        return itin

    async def list_by_conversation(self, conversation_id: int) -> list[Itinerary]:
        return [i for i in self.data if i.conversation_id == conversation_id]

    async def get_for_conversation(self, conversation_id: int, itinerary_id: int):
        return next(
            (
                i
                for i in self.data
                if i.id == itinerary_id and i.conversation_id == conversation_id
            ),
            None,
        )


class FakeItemRepo:
    def __init__(self) -> None:
        self.by_itinerary: dict[int, list[ItineraryItem]] = {}

    async def bulk_add(self, items: list[ItineraryItem]) -> None:
        self.by_itinerary.setdefault(items[0].itinerary_id, []).extend(items)

    async def list_by_itinerary(self, itinerary_id: int) -> list[ItineraryItem]:
        return list(self.by_itinerary.get(itinerary_id, []))


class FakeEventRepo:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def append(self, **kwargs) -> dict:
        self.events.append(kwargs)
        return kwargs


SNAPSHOT = {
    "duration_hours": 8,
    "budget": 300,
    "interests": ["历史"],
    "origin": {"name": "徐州站", "lat": 34.2643, "lng": 117.201},
    "companions": None,
    "pinned_pois": [],
    "short_trip": False,
    "visit_date": "2026-10-21",
}


def make_plan(goal: Goal, costs=(60, 90), ticket=50) -> ItineraryPlan:
    items = []
    minute = 540
    for i, _ in enumerate(costs):
        items.append(
            PlanItem(
                poi_id=i + 1,
                name=f"景点{i + 1}",
                kind="sight",
                start_minute=minute,
                end_minute=minute + costs[i],
                ticket_price=ticket,
            )
        )
        minute += costs[i] + 30
    return ItineraryPlan(
        goal=goal,
        items=items,
        segments=[
            TransportSegment(
                from_poi_id=-1,
                to_poi_id=1,
                mode=TransportMode.TRANSIT,
                minutes=25,
                fare=2,
            )
        ],
        total_cost=102,
        ticket_cost=100,
        transit_cost=2,
        transit_minutes=25,
        total_minutes=420,
    )


def make_service():
    itin_repo = FakeItineraryRepo()
    item_repo = FakeItemRepo()
    event_repo = FakeEventRepo()
    service = ItineraryService(itin_repo, item_repo, event_repo)
    return service, itin_repo, item_repo, event_repo


async def test_save_draft_plans_creates_three_drafts_with_same_lineage():
    service, itin_repo, item_repo, _ = make_service()
    plans = [make_plan(Goal.SAVE_MONEY), make_plan(Goal.SAVE_TIME), make_plan(Goal.BALANCED)]
    saved = await service.save_draft_plans(1, plans, SNAPSHOT)
    assert len(saved) == 3
    assert all(i.status == DRAFT for i in saved)
    assert all(i.plan_type in ("省钱", "省时", "均衡") for i in saved)
    lineages = {i.version_lineage for i in saved}
    assert lineages == {1}
    assert len(item_repo.by_itinerary) == 3
    items = item_repo.by_itinerary[saved[0].id]
    assert items[0].activity_type == "游览"
    assert items[0].transport_mode == "公交"
    assert items[0].transport_minutes == 25
    assert items[0].cost_meta["is_reference"] is False


async def test_save_draft_plans_replaces_old_drafts_but_keeps_confirmed():
    service, itin_repo, _, _ = make_service()
    first = await service.save_draft_plans(1, [make_plan(Goal.SAVE_MONEY)], SNAPSHOT)
    confirmed = first[0]
    confirmed.status = CONFIRMED
    stale = Itinerary(conversation_id=1, plan_type="省时", status=DRAFT,
                      total_cost=10, total_minutes=60, request_snapshot=SNAPSHOT)
    await itin_repo.add(stale)

    second = await service.save_draft_plans(1, [make_plan(Goal.BALANCED)], SNAPSHOT)

    assert confirmed.status == CONFIRMED
    assert stale.status == REPLACED
    assert second[0].version_lineage == 2
    assert second[0].status == DRAFT


async def test_confirm_plan_transfers_state_and_appends_event():
    service, itin_repo, _, event_repo = make_service()
    saved = await service.save_draft_plans(1, [make_plan(Goal.SAVE_MONEY)], SNAPSHOT)
    confirmed = await service.confirm_plan(1, saved[0].id)
    assert confirmed.status == CONFIRMED
    assert len(event_repo.events) == 1
    event = event_repo.events[0]
    assert event["event_type"] == "用户调整"
    assert event["payload"]["action"] == "confirm"
    assert event["affected_itinerary_ids"] == [saved[0].id]


async def test_confirm_rejects_non_draft_status():
    service, itin_repo, _, _ = make_service()
    saved = await service.save_draft_plans(1, [make_plan(Goal.SAVE_MONEY)], SNAPSHOT)
    saved[0].status = REPLACED
    with pytest.raises(BizError) as exc_info:
        await service.confirm_plan(1, saved[0].id)
    assert exc_info.value.code == ErrorCode.STATE_CONFLICT


async def test_confirm_twice_conflicts():
    service, _, _, _ = make_service()
    saved = await service.save_draft_plans(1, [make_plan(Goal.SAVE_MONEY)], SNAPSHOT)
    await service.confirm_plan(1, saved[0].id)
    with pytest.raises(BizError) as exc_info:
        await service.confirm_plan(1, saved[0].id)
    assert exc_info.value.code == ErrorCode.STATE_CONFLICT


async def test_confirm_not_found():
    service, _, _, _ = make_service()
    with pytest.raises(BizError) as exc_info:
        await service.confirm_plan(1, 999)
    assert exc_info.value.code == ErrorCode.NOT_FOUND


async def test_confirm_pending_replaces_confirmed_sibling_in_same_lineage():
    service, itin_repo, _, event_repo = make_service()
    saved = await service.save_draft_plans(1, [make_plan(Goal.SAVE_MONEY)], SNAPSHOT)
    original = saved[0]
    await service.confirm_plan(1, original.id)

    pending = await service.persist_pending_plan(
        1, original, make_plan(Goal.SAVE_MONEY), SNAPSHOT
    )
    assert pending.status == PENDING
    assert pending.version_lineage == original.version_lineage
    assert original.status == CONFIRMED

    confirmed_new = await service.confirm_plan(1, pending.id)
    assert confirmed_new.status == CONFIRMED
    assert original.status == REPLACED
    actions = [e["payload"]["action"] for e in event_repo.events]
    assert actions == ["confirm", "replan_suggestion", "confirm"]
    assert event_repo.events[-1]["payload"]["replaced_itinerary_ids"] == [original.id]


async def test_persist_pending_replaces_older_pending_same_lineage():
    service, itin_repo, _, _ = make_service()
    saved = await service.save_draft_plans(1, [make_plan(Goal.SAVE_MONEY)], SNAPSHOT)
    original = saved[0]
    first_pending = await service.persist_pending_plan(1, original, make_plan(Goal.SAVE_MONEY), SNAPSHOT)
    second_pending = await service.persist_pending_plan(1, original, make_plan(Goal.SAVE_MONEY), SNAPSHOT)
    assert first_pending.status == REPLACED
    assert second_pending.status == PENDING
    assert original.status == DRAFT


async def test_plan_items_roundtrip():
    plan = make_plan(Goal.BALANCED)
    items = plan_to_items(1, plan, SNAPSHOT)
    itinerary = Itinerary(
        conversation_id=1,
        plan_type="均衡",
        status=DRAFT,
        total_cost=plan.total_cost,
        total_minutes=plan.total_minutes,
        request_snapshot=SNAPSHOT,
    )
    itinerary.id = 1
    restored = items_to_plan(itinerary, items, {1: "景点1", 2: "景点2"})
    assert [i.poi_id for i in restored.items] == [i.poi_id for i in plan.items]
    assert restored.total_cost == plan.total_cost
    assert restored.goal == Goal.BALANCED


async def test_to_plan_detail_money_strings_and_meta():
    plan = make_plan(Goal.SAVE_MONEY)
    items = plan_to_items(7, plan, SNAPSHOT)
    itinerary = Itinerary(
        id=7,
        conversation_id=1,
        plan_type="省钱",
        status=DRAFT,
        total_cost=102,
        total_minutes=420,
        request_snapshot=SNAPSHOT,
        version_lineage=3,
    )
    detail = to_plan_detail(itinerary, items)
    assert detail["plan_id"] == "7"
    assert detail["total_cost"] == "102.00"
    assert detail["transport_minutes"] == 25
    assert detail["items"][0]["estimated_cost"] == "50.00"
    assert detail["items"][0]["cost_meta"]["is_reference"] is False
    assert detail["highlights"] and detail["suited_for"]
    assert money(35) == "35.00"