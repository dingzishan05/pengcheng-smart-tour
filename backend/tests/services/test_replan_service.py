from __future__ import annotations

import asyncio

import pytest

from app.core.errors import BizError, ErrorCode
from app.models import Itinerary
from app.optimizer.models import (
    CandidatePoi,
    Constraints,
    Goal,
    ItineraryItem as PlanItem,
    ItineraryPlan,
    OptimizeProblem,
    RouteEdge,
    RouteOption,
)
from app.services.replan_service import (
    RebuiltProblem,
    ReplanChanges,
    ReplanService,
    parse_party_size,
)
from app.services.itinerary_service import (
    CONFIRMED,
    DRAFT,
    PENDING,
    REPLACED,
    plan_to_items,
)
from tests.services.test_itinerary_service import (
    SNAPSHOT,
    FakeEventRepo,
    FakeItineraryRepo,
    FakeItemRepo,
    make_plan,
)


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def commit(self):
        return None


def fake_factory():
    return FakeSession()


def build_problem(budget=300) -> OptimizeProblem:
    candidates = []
    for i in range(1, 7):
        candidates.append(
            CandidatePoi(
                poi_id=i,
                name=f"景点{i}",
                visit_minutes=90,
                ticket_price=60 if i <= 2 else 0,
                interest_score=0.7,
                tags=["历史"],
                lat=34.26,
                lng=117.19,
                indoor=True,
                status_active=True,
                open_windows={2: (540, 1080)},
            )
        )
    edge = RouteEdge(
        walk=RouteOption(minutes=20, distance_meters=1500),
        transit=RouteOption(minutes=10, distance_meters=1500),
        drive=RouteOption(minutes=6, distance_meters=1500),
    )
    matrix = {}
    for a in (-1, *range(1, 7)):
        for b in (-1, *range(1, 7)):
            if a != b:
                matrix[(a, b)] = edge
    constraints = Constraints(
        tour_date=__import__("datetime").date(2026, 10, 21),
        budget=budget,
        hours=8,
        origin_lat=34.26,
        origin_lng=117.19,
    )
    return OptimizeProblem(candidates=candidates, route_matrix=matrix, constraints=constraints)


def make_builder(problem=None, names=None, degraded=False):
    async def builder(session, snapshot, must_include_ids):
        return RebuiltProblem(
            problem=problem or build_problem(),
            names=names or {i: f"景点{i}" for i in range(1, 7)},
            degraded=degraded,
        )

    return builder


async def test_parse_party_size():
    assert parse_party_size(None) == 1
    assert parse_party_size("一家三口") == 3
    assert parse_party_size("夫妻两人") == 2
    assert parse_party_size("我们4个人") == 4


async def test_refresh_budget_change_creates_pending_and_diff(monkeypatch):
    itin_repo = FakeItineraryRepo()
    item_repo = FakeItemRepo()
    event_repo = FakeEventRepo()
    monkeypatch.setattr("app.services.replan_service.ItineraryRepository", lambda session: itin_repo)
    monkeypatch.setattr("app.services.replan_service.ItineraryItemRepository", lambda session: item_repo)
    monkeypatch.setattr("app.services.replan_service.ChangeEventRepository", lambda session: event_repo)

    original = Itinerary(
        conversation_id=1,
        plan_type="省钱",
        status=CONFIRMED,
        total_cost=302,
        total_minutes=420,
        request_snapshot=SNAPSHOT,
        version_lineage=1,
    )
    await itin_repo.add(original)
    plan = make_plan(Goal.SAVE_MONEY)
    await item_repo.bulk_add(plan_to_items(original.id, plan, SNAPSHOT))

    service = ReplanService(problem_builder=make_builder())
    outcome = await service.refresh(
        fake_factory, 1, original.id, "user_change", ReplanChanges(budget=150)
    )

    assert outcome["new_plan"] is not None
    assert outcome["new_plan"]["status"] == PENDING
    assert outcome["old_plan"]["status"] == CONFIRMED
    assert outcome["conflict"] is None
    diff = outcome["diff"]
    assert set(diff) == {"removed", "added", "cost_delta", "reason"}
    assert diff["cost_delta"].startswith("-") or float(diff["cost_delta"]) <= 0
    assert original.status == CONFIRMED
    assert len(event_repo.events) == 1
    assert event_repo.events[0]["event_type"] == "用户调整"


async def test_refresh_confirmed_plan_not_overwritten(monkeypatch):
    itin_repo = FakeItineraryRepo()
    item_repo = FakeItemRepo()
    event_repo = FakeEventRepo()
    monkeypatch.setattr("app.services.replan_service.ItineraryRepository", lambda session: itin_repo)
    monkeypatch.setattr("app.services.replan_service.ItineraryItemRepository", lambda session: item_repo)
    monkeypatch.setattr("app.services.replan_service.ChangeEventRepository", lambda session: event_repo)

    original = Itinerary(
        conversation_id=1,
        plan_type="省钱",
        status=CONFIRMED,
        total_cost=302,
        total_minutes=420,
        request_snapshot=SNAPSHOT,
        version_lineage=1,
    )
    await itin_repo.add(original)
    await item_repo.bulk_add(plan_to_items(original.id, make_plan(Goal.SAVE_MONEY), SNAPSHOT))

    service = ReplanService(problem_builder=make_builder())
    await service.refresh(fake_factory, 1, original.id, "user_change", ReplanChanges(budget=150))

    stored = await itin_repo.get_for_conversation(1, original.id)
    assert stored.status == CONFIRMED
    assert stored.total_cost == 302
    items_before = [i.estimated_cost for i in item_repo.by_itinerary[original.id]]
    new_pending = [i for i in itin_repo.data if i.status == PENDING]
    assert len(new_pending) == 1
    assert new_pending[0].version_lineage == original.version_lineage
    assert [i.estimated_cost for i in item_repo.by_itinerary[original.id]] == items_before


async def test_refresh_infeasible_budget_returns_conflict(monkeypatch):
    itin_repo = FakeItineraryRepo()
    item_repo = FakeItemRepo()
    event_repo = FakeEventRepo()
    monkeypatch.setattr("app.services.replan_service.ItineraryRepository", lambda session: itin_repo)
    monkeypatch.setattr("app.services.replan_service.ItineraryItemRepository", lambda session: item_repo)
    monkeypatch.setattr("app.services.replan_service.ChangeEventRepository", lambda session: event_repo)

    original = Itinerary(
        conversation_id=1,
        plan_type="省钱",
        status=CONFIRMED,
        total_cost=302,
        total_minutes=420,
        request_snapshot=SNAPSHOT,
        version_lineage=1,
    )
    await itin_repo.add(original)
    await item_repo.bulk_add(plan_to_items(original.id, make_plan(Goal.SAVE_MONEY), SNAPSHOT))

    service = ReplanService(problem_builder=make_builder())
    outcome = await service.refresh(
        fake_factory, 1, original.id, "user_change", ReplanChanges(budget=1)
    )

    assert outcome["new_plan"] is None
    assert outcome["conflict"] is not None
    assert outcome["conflict"]["kept_original"] is True
    advice = outcome["conflict"]["advice"]
    assert advice["reasons"]
    assert advice["min_budget_suggestion"] is not None
    assert original.status == CONFIRMED
    assert not [i for i in itin_repo.data if i.status == PENDING]


async def test_refresh_replace_item(monkeypatch):
    itin_repo = FakeItineraryRepo()
    item_repo = FakeItemRepo()
    event_repo = FakeEventRepo()
    monkeypatch.setattr("app.services.replan_service.ItineraryRepository", lambda session: itin_repo)
    monkeypatch.setattr("app.services.replan_service.ItineraryItemRepository", lambda session: item_repo)
    monkeypatch.setattr("app.services.replan_service.ChangeEventRepository", lambda session: event_repo)

    original = Itinerary(
        conversation_id=1,
        plan_type="均衡",
        status=CONFIRMED,
        total_cost=302,
        total_minutes=420,
        request_snapshot=SNAPSHOT,
        version_lineage=1,
    )
    await itin_repo.add(original)
    await item_repo.bulk_add(plan_to_items(original.id, make_plan(Goal.BALANCED), SNAPSHOT))

    service = ReplanService(problem_builder=make_builder())
    outcome = await service.refresh(
        fake_factory,
        1,
        original.id,
        "user_change",
        ReplanChanges(replace={"from_poi_id": 1, "to_poi_id": 3}),
    )
    assert outcome["new_plan"] is not None
    assert outcome["diff"]["reason"]
    assert outcome["conflict"] is None


async def test_refresh_invalid_trigger_rejected():
    service = ReplanService(problem_builder=make_builder())
    with pytest.raises(BizError) as exc_info:
        await service.refresh(fake_factory, 1, 1, "unknown", ReplanChanges(budget=100))
    assert exc_info.value.code == ErrorCode.PARAM_INVALID


async def test_refresh_plan_not_found(monkeypatch):
    itin_repo = FakeItineraryRepo()
    monkeypatch.setattr("app.services.replan_service.ItineraryRepository", lambda session: itin_repo)
    monkeypatch.setattr("app.services.replan_service.ItineraryItemRepository", lambda session: FakeItemRepo())
    monkeypatch.setattr("app.services.replan_service.ChangeEventRepository", lambda session: FakeEventRepo())
    service = ReplanService(problem_builder=make_builder())
    with pytest.raises(BizError) as exc_info:
        await service.refresh(fake_factory, 1, 42, "user_change", ReplanChanges(budget=100))
    assert exc_info.value.code == ErrorCode.NOT_FOUND


async def test_refresh_conversation_lock_serializes(monkeypatch):
    itin_repo = FakeItineraryRepo()
    item_repo = FakeItemRepo()
    event_repo = FakeEventRepo()
    monkeypatch.setattr("app.services.replan_service.ItineraryRepository", lambda session: itin_repo)
    monkeypatch.setattr("app.services.replan_service.ItineraryItemRepository", lambda session: item_repo)
    monkeypatch.setattr("app.services.replan_service.ChangeEventRepository", lambda session: event_repo)

    original = Itinerary(
        conversation_id=1,
        plan_type="省钱",
        status=CONFIRMED,
        total_cost=302,
        total_minutes=420,
        request_snapshot=SNAPSHOT,
        version_lineage=1,
    )
    await itin_repo.add(original)
    await item_repo.bulk_add(plan_to_items(original.id, make_plan(Goal.SAVE_MONEY), SNAPSHOT))

    service = ReplanService(problem_builder=make_builder())
    await asyncio.gather(
        service.refresh(fake_factory, 1, original.id, "user_change", ReplanChanges(budget=150)),
        service.refresh(fake_factory, 1, original.id, "user_change", ReplanChanges(budget=200)),
    )
    pendings = [i for i in itin_repo.data if i.status == PENDING]
    replaced = [i for i in itin_repo.data if i.status == REPLACED]
    assert len(pendings) == 1
    assert len(replaced) == 1
    assert original.status == CONFIRMED

@pytest.mark.parametrize(
    "changes",
    [
        ReplanChanges(price_hike={"poi_id": 1, "new_price": 80.0}),
        ReplanChanges(poi_closed={"poi_id": 1}),
        ReplanChanges(heavy_rain=True),
    ],
    ids=["price_hike", "poi_closed", "heavy_rain"],
)
async def test_refresh_confirmed_not_overwritten_on_data_change(monkeypatch, changes):
    itin_repo = FakeItineraryRepo()
    item_repo = FakeItemRepo()
    event_repo = FakeEventRepo()
    monkeypatch.setattr("app.services.replan_service.ItineraryRepository", lambda session: itin_repo)
    monkeypatch.setattr("app.services.replan_service.ItineraryItemRepository", lambda session: item_repo)
    monkeypatch.setattr("app.services.replan_service.ChangeEventRepository", lambda session: event_repo)

    original = Itinerary(
        conversation_id=1,
        plan_type="省钱",
        status=CONFIRMED,
        total_cost=302,
        total_minutes=420,
        request_snapshot=SNAPSHOT,
        version_lineage=1,
    )
    await itin_repo.add(original)
    await item_repo.bulk_add(plan_to_items(original.id, make_plan(Goal.SAVE_MONEY), SNAPSHOT))
    items_before = [i.estimated_cost for i in item_repo.by_itinerary[original.id]]

    service = ReplanService(problem_builder=make_builder())
    outcome = await service.refresh(fake_factory, 1, original.id, "data_change", changes)

    stored = await itin_repo.get_for_conversation(1, original.id)
    assert stored.status == CONFIRMED
    assert stored.total_cost == 302
    assert [i.estimated_cost for i in item_repo.by_itinerary[original.id]] == items_before

    if outcome["conflict"] is not None:
        assert outcome["new_plan"] is None
        assert outcome["conflict"]["kept_original"] is True
        assert not [i for i in itin_repo.data if i.status == PENDING]
    else:
        new_plan = outcome["new_plan"]
        assert new_plan is not None
        assert int(new_plan["plan_id"]) != original.id
        assert new_plan["status"] == PENDING