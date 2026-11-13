from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timezone

from pydantic import BaseModel

from app.core.errors import BizError, ErrorCode
from app.models import Itinerary, Poi, PoiHours, PriceRecord, TransportRoute
from app.optimizer.models import (
    Constraints,
    Goal,
    OptimizeProblem,
    RouteEdge,
    RouteOption,
)
from app.optimizer.replan import (
    replan_budget_change,
    replan_data_change,
    replan_replace_item,
)
from app.repositories.itinerary_repo import (
    ChangeEventRepository,
    ItineraryItemRepository,
    ItineraryRepository,
)
from app.repositories.poi_repo import (
    PoiHoursRepository,
    PoiRepository,
    PriceRepository,
    TransportRouteRepository,
)
from app.services.itinerary_service import (
    ItineraryService,
    items_to_plan,
    to_plan_detail,
)

MAX_ROUTE_POIS = 12
WALK_M_PER_MIN = 75
TRANSIT_M_PER_MIN = 400
DRIVE_M_PER_MIN = 500
GOAL_BY_TYPE = {"省钱": Goal.SAVE_MONEY, "省时": Goal.SAVE_TIME, "均衡": Goal.BALANCED}

DEFAULT_ORIGIN = {"name": "彭城广场（市中心）", "lat": 34.2570, "lng": 117.1840}


class RebuiltProblem(BaseModel):
    problem: OptimizeProblem
    names: dict[int, str]
    degraded: bool
    data_time: datetime | None = None


class ReplanChanges(BaseModel):
    budget: float | None = None
    replace: dict | None = None
    price_hike: dict | None = None
    poi_closed: dict | None = None
    heavy_rain: bool = False


def parse_party_size(companions: str | None) -> int:
    if not companions:
        return 1
    if any(w in companions for w in ("一家", "家人", "孩子", "小孩", "亲子", "老人")):
        return 3
    if any(w in companions for w in ("两个人", "两人", "情侣", "夫妻", "朋友")):
        return 2
    digits = re.findall(r"[1-9]", companions)
    return int(digits[0]) if digits else 1


def _snapshot_date(snapshot: dict) -> date:
    raw = snapshot.get("visit_date")
    if raw:
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            pass
    return date.today()


def _estimate_edge(distance_meters: int) -> RouteEdge:
    return RouteEdge(
        walk=RouteOption(minutes=max(5, distance_meters // WALK_M_PER_MIN), distance_meters=distance_meters),
        transit=RouteOption(minutes=max(8, distance_meters // TRANSIT_M_PER_MIN), distance_meters=distance_meters),
        drive=RouteOption(minutes=max(4, distance_meters // DRIVE_M_PER_MIN), distance_meters=distance_meters),
    )


def _snapshot_constraints(snapshot: dict, budget_override: float | None = None) -> Constraints:
    origin = snapshot.get("origin") or DEFAULT_ORIGIN
    return Constraints(
        tour_date=_snapshot_date(snapshot),
        budget=float(budget_override if budget_override is not None else snapshot.get("budget") or 0),
        hours=float(snapshot.get("duration_hours") or 4.0),
        start_minute=9 * 60,
        origin_name=origin.get("name", "出发地"),
        origin_lat=float(origin.get("lat", DEFAULT_ORIGIN["lat"])),
        origin_lng=float(origin.get("lng", DEFAULT_ORIGIN["lng"])),
        party_size=parse_party_size(snapshot.get("companions")),
        interest_tags=list(snapshot.get("interests") or []),
    )


async def default_problem_builder(
    session, snapshot: dict, must_include_ids: list[int]
) -> RebuiltProblem:
    poi_repo = PoiRepository(session)
    hours_repo = PoiHoursRepository(session)
    price_repo = PriceRepository(session)
    route_repo = TransportRouteRepository(session)

    visit = _snapshot_date(snapshot)
    weekday = visit.weekday()
    pois: list[Poi] = await poi_repo.list_active()

    from app.optimizer.models import CandidatePoi

    candidates: list[CandidatePoi] = []
    names: dict[int, str] = {}
    coordinates: dict[int, tuple[float, float]] = {-1: (0.0, 0.0)}
    constraints = _snapshot_constraints(snapshot)
    coordinates[-1] = (constraints.origin_lat, constraints.origin_lng)

    for poi in pois:
        hours: PoiHours | None = await hours_repo.get_hours(poi.id, visit)
        price: PriceRecord | None = await price_repo.get_current(poi.id)
        names[poi.id] = poi.name
        coordinates[poi.id] = (float(poi.latitude), float(poi.longitude))
        windows: dict[int, tuple[int, int]] = {}
        is_reference = False
        if hours is not None and not hours.closed and hours.open_time and hours.close_time:
            windows[weekday] = (hours.open_time.hour * 60 + hours.open_time.minute, hours.close_time.hour * 60 + hours.close_time.minute)
        ticket = float(price.amount) if price is not None else 0.0
        if price is not None and price.valid_until < visit:
            is_reference = True
        candidates.append(
            CandidatePoi(
                poi_id=poi.id,
                name=poi.name,
                visit_minutes=poi.visit_minutes,
                ticket_price=ticket,
                is_reference_price=is_reference,
                interest_score=0.6,
                tags=list(poi.tags),
                lat=float(poi.latitude),
                lng=float(poi.longitude),
                indoor=poi.indoor,
                status_active=True,
                open_windows=windows,
            )
        )

    interest_set = set(constraints.interest_tags)
    must_set = set(must_include_ids)

    def _rank(c: CandidatePoi) -> float:
        overlap = len(set(c.tags) & interest_set) if interest_set else 0.5
        bonus = 1.0 if c.poi_id in must_set else 0.0
        return overlap + bonus + c.interest_score

    ranked = sorted(candidates, key=_rank, reverse=True)
    selected_ids = {c.poi_id for c in ranked[:MAX_ROUTE_POIS]} | must_set

    routes: list[TransportRoute] = await route_repo.list_all()
    route_map: dict[tuple[int, int, str], TransportRoute] = {
        (r.origin_poi_id, r.dest_poi_id, r.mode): r for r in routes
    }

    matrix: dict[tuple[int, int], RouteEdge] = {}
    degraded = False
    from app.optimizer.models import haversine_km

    for a in coordinates:
        if a != -1 and a not in selected_ids:
            continue
        for b in coordinates:
            if a == b or (b != -1 and b not in selected_ids):
                continue
            edge = RouteEdge()
            straight = int(
                haversine_km(coordinates[a][0], coordinates[a][1], coordinates[b][0], coordinates[b][1]) * 1000
            )
            for mode, field in (("步行", "walk"), ("公交", "transit"), ("驾车", "drive")):
                row = route_map.get((a, b, mode))
                if row is not None and row.duration_minutes is not None:
                    option = RouteOption(minutes=row.duration_minutes, distance_meters=row.distance_meters or straight)
                else:
                    option = _estimate_edge(straight).model_dump()[field]
                    option = RouteOption(**option)
                    degraded = True
                setattr(edge, field, option)
            matrix[(a, b)] = edge

    return RebuiltProblem(
        problem=OptimizeProblem(
            candidates=candidates,
            route_matrix=matrix,
            constraints=constraints,
        ),
        names=names,
        degraded=degraded,
        data_time=datetime.now(timezone.utc) if degraded else None,
    )


class ReplanService:
    _conversation_locks: dict[int, asyncio.Lock] = {}
    _registry_guard = asyncio.Lock()

    def __init__(
        self,
        problem_builder=default_problem_builder,
        itinerary_service: ItineraryService | None = None,
    ) -> None:
        self.problem_builder = problem_builder
        self.itinerary_service = itinerary_service

    @classmethod
    async def _lock_for(cls, conversation_id: int) -> asyncio.Lock:
        async with cls._registry_guard:
            lock = cls._conversation_locks.get(conversation_id)
            if lock is None:
                lock = asyncio.Lock()
                cls._conversation_locks[conversation_id] = lock
            return lock

    async def refresh(
        self,
        session_factory,
        conversation_id: int,
        plan_id: int,
        trigger: str,
        changes: ReplanChanges,
    ) -> dict:
        if trigger not in ("user_change", "data_change"):
            raise BizError(ErrorCode.PARAM_INVALID, "trigger 必须是 user_change 或 data_change")
        lock = await self._lock_for(conversation_id)
        async with lock:
            async with session_factory() as session:
                itin_repo = ItineraryRepository(session)
                item_repo = ItineraryItemRepository(session)
                itinerary = await itin_repo.get_for_conversation(conversation_id, plan_id)
                if itinerary is None:
                    raise BizError(
                        ErrorCode.NOT_FOUND,
                        "行程不存在或不属于当前会话",
                        plan_id=str(plan_id),
                    )
                items = await item_repo.list_by_itinerary(itinerary.id)
                old_items = list(items)

                rebuilt = await self.problem_builder(
                    session, dict(itinerary.request_snapshot or {}), [i.poi_id for i in old_items if i.poi_id]
                )
                original_plan = items_to_plan(itinerary, old_items, rebuilt.names)
                old_detail = to_plan_detail(itinerary, old_items)
                snapshot = dict(itinerary.request_snapshot or {})

            outcome = self._run_replan(original_plan, rebuilt, changes)

            if outcome.conflict is not None:
                async with session_factory() as session:
                    event_repo = ChangeEventRepository(session)
                    await event_repo.append(
                        event_type="用户调整",
                        entity_type="itinerary",
                        entity_id=itinerary.id,
                        payload={"action": "replan_conflict", "reason": outcome.conflict["advice"]["reasons"]},
                        affected_itinerary_ids=[itinerary.id],
                    )
                    await session.commit()
                return {
                    "new_plan": None,
                    "old_plan": old_detail,
                    "diff": None,
                    "conflict": outcome.conflict,
                    "data_time": None,
                }

            async with session_factory() as session:
                itin_repo = ItineraryRepository(session)
                item_repo = ItineraryItemRepository(session)
                event_repo = ChangeEventRepository(session)
                service = ItineraryService(itin_repo, item_repo, event_repo)
                pending = await service.persist_pending_plan(
                    conversation_id=conversation_id,
                    original=itinerary,
                    new_plan=outcome.new_plan,
                    request_snapshot=snapshot,
                    data_time=rebuilt.data_time,
                )
                new_items = await item_repo.list_by_itinerary(pending.id)
                new_detail = to_plan_detail(pending, new_items)
                await session.commit()

            return {
                "new_plan": new_detail,
                "old_plan": old_detail,
                "diff": {
                    "removed": [rebuilt.names.get(pid, f"POI-{pid}") for pid in outcome.diff.removed_poi_ids],
                    "added": [rebuilt.names.get(pid, f"POI-{pid}") for pid in outcome.diff.added_poi_ids],
                    "cost_delta": f"{outcome.diff.cost_delta:.2f}",
                    "reason": outcome.diff.detail,
                },
                "conflict": None,
                "data_time": rebuilt.data_time.isoformat() if rebuilt.data_time else None,
            }

    def _run_replan(self, original_plan, rebuilt: RebuiltProblem, changes: ReplanChanges):
        from app.optimizer.models import PlanDiff, ReplanResult

        problem = rebuilt.problem
        if changes.budget is not None:
            result = replan_budget_change(original_plan, float(changes.budget), problem)
            if not [i for i in result.new_plan.items if i.kind == "sight"]:
                min_budget = round(
                    sum(i.ticket_price for i in original_plan.items if i.kind == "sight")
                    - (original_plan.total_cost - original_plan.transit_cost),
                    2,
                )
                advice_budget = max(min_budget, round(problem.constraints.budget * 0.8, 2))
                return _ConflictOutcome(
                    advice={
                        "reasons": [f"预算 {changes.budget} 元低于当前方案最低可行组合"],
                        "min_budget_suggestion": f"{advice_budget:.2f}",
                    }
                )
            return _OkOutcome(result)
        if changes.replace is not None:
            from_id = int(changes.replace.get("from_poi_id", 0))
            to_id = int(changes.replace.get("to_poi_id", 0))
            result = replan_replace_item(original_plan, from_id, problem, exclude_poi_ids=[to_id])
            if result is None:
                raise BizError(
                    ErrorCode.PARAM_INVALID,
                    f"方案中不存在 POI-{from_id}，无法替换",
                )
            return _OkOutcome(result)
        if changes.price_hike is not None:
            poi_id = int(changes.price_hike.get("poi_id", 0))
            new_price = float(changes.price_hike.get("new_price", 0))
            result = replan_data_change(original_plan, "price_hike", poi_id, new_price, problem)
        elif changes.poi_closed is not None:
            poi_id = int(changes.poi_closed.get("poi_id", 0))
            result = replan_data_change(original_plan, "poi_closed", poi_id, None, problem)
        elif changes.heavy_rain:
            result = replan_data_change(original_plan, "heavy_rain", None, None, problem)
        else:
            raise BizError(ErrorCode.PARAM_INVALID, "changes 需要至少一项变更内容")
        if result is None:
            return _ConflictOutcome(
                advice={
                    "reasons": ["该数据变化不影响当前行程，无需重规划"],
                    "min_budget_suggestion": None,
                }
            )
        return _OkOutcome(result)


class _OkOutcome:
    def __init__(self, result) -> None:
        self.new_plan = result.new_plan
        self.diff = result.diff
        self.reason = result.reason
        self.conflict = None


class _ConflictOutcome:
    def __init__(self, advice: dict) -> None:
        self.new_plan = None
        self.diff = None
        self.conflict = {"advice": advice, "kept_original": True}