from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from app.core.errors import BizError, ErrorCode
from app.models import Itinerary, ItineraryItem
from app.optimizer.models import ItineraryItem as PlanItem, ItineraryPlan
from app.repositories.itinerary_repo import (
    ChangeEventRepository,
    ItineraryItemRepository,
    ItineraryRepository,
)

DRAFT = "草稿"
CONFIRMED = "已确认"
REPLACED = "已被替换"
PENDING = "挂起建议"

GOAL_TO_TYPE = {"save_money": "省钱", "save_time": "省时", "balanced": "均衡"}
TYPE_TO_GOAL = {v: k for k, v in GOAL_TO_TYPE.items()}

ACTIVITY_MAP = {"sight": "游览", "meal": "用餐", "transport": "交通"}

PLAN_HIGHLIGHTS = {
    "省钱": ["以免费/低价景点为主", "优先公交与步行"],
    "省时": ["路线紧凑耗时最短", "就近安排减少折返"],
    "均衡": ["花费与时间均衡", "覆盖兴趣匹配度最高"],
}
PLAN_SUITED_FOR = {
    "省钱": "预算敏感型游客",
    "省时": "时间紧张的商务/半日游客",
    "均衡": "首次到访、希望全面体验的游客",
}


def money(value: float) -> str:
    return f"{float(value):.2f}"


def _visit_date_of(snapshot: dict) -> datetime:
    """行程时间以徐州当地时间（UTC+8）为基准，避免时间轴整体偏移 8 小时。"""
    local_tz = timezone(timedelta(hours=8))
    raw = snapshot.get("visit_date")
    if raw:
        try:
            parsed = datetime.fromisoformat(str(raw))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=local_tz)
            return parsed.astimezone(local_tz).replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            pass
    return datetime.now(local_tz).replace(hour=0, minute=0, second=0, microsecond=0)


def plan_to_items(
    itinerary_id: int, plan: ItineraryPlan, snapshot: dict
) -> list[ItineraryItem]:
    base = _visit_date_of(snapshot)
    seg_by_to = {s.to_poi_id: s for s in plan.segments}
    ordered = sorted(plan.items, key=lambda i: i.start_minute)
    items: list[ItineraryItem] = []
    for seq, item in enumerate(ordered, start=1):
        # 午餐项与所在景点共用 poi_id，不能套用该景点的交通段，否则交通耗时会重复累加。
        seg = seg_by_to.get(item.poi_id) if item.kind != "meal" else None
        items.append(
            ItineraryItem(
                itinerary_id=itinerary_id,
                seq=seq,
                # 午餐不是景点：不挂 poi_id，避免时间轴把“用餐”显示成某个景点。
                poi_id=None if item.kind == "meal" else item.poi_id,
                activity_type=ACTIVITY_MAP.get(item.kind, "游览"),
                start_at=base + timedelta(minutes=item.start_minute),
                end_at=base + timedelta(minutes=item.end_minute),
                transport_mode=seg.mode.value if seg else None,
                transport_minutes=seg.minutes if seg else None,
                estimated_cost=round(item.ticket_price, 2),
                cost_meta={
                    "source_channel": "优化引擎",
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                    "is_reference": bool(item.is_reference_price),
                },
            )
        )
    return items


def items_to_plan(itinerary: Itinerary, items: list[ItineraryItem], names: dict[int, str]) -> ItineraryPlan:
    from app.optimizer.models import Goal

    base = _visit_date_of(itinerary.request_snapshot or {})
    plan_items: list[PlanItem] = []
    for it in items:
        if it.activity_type == "交通":
            continue
        start = int((it.start_at - base).total_seconds() // 60)
        end = int((it.end_at - base).total_seconds() // 60)
        plan_items.append(
            PlanItem(
                poi_id=it.poi_id or 0,
                name=names.get(it.poi_id or 0, f"POI-{it.poi_id}"),
                kind="meal" if it.activity_type == "用餐" else "sight",
                start_minute=start,
                end_minute=end,
                ticket_price=float(it.estimated_cost or 0),
                is_reference_price=bool((it.cost_meta or {}).get("is_reference")),
            )
        )
    return ItineraryPlan(
        goal=Goal(TYPE_TO_GOAL.get(itinerary.plan_type, "balanced")),
        items=plan_items,
        total_cost=float(itinerary.total_cost),
        total_minutes=itinerary.total_minutes,
    )


def to_item_dto(item: ItineraryItem) -> dict:
    meta = item.cost_meta or {}
    local_tz = timezone(timedelta(hours=8))
    return {
        "seq": item.seq,
        "activity_type": item.activity_type,
        "poi_id": str(item.poi_id) if item.poi_id is not None else None,
        "start_at": item.start_at.astimezone(local_tz).isoformat() if item.start_at else None,
        "end_at": item.end_at.astimezone(local_tz).isoformat() if item.end_at else None,
        "transport_mode": item.transport_mode,
        "transport_minutes": item.transport_minutes,
        "estimated_cost": money(item.estimated_cost),
        "cost_meta": {
            "source_channel": meta.get("source_channel", ""),
            "collected_at": meta.get("collected_at"),
            "is_reference": bool(meta.get("is_reference")),
        },
    }


def to_plan_detail(itinerary: Itinerary, items: list[ItineraryItem]) -> dict:
    return {
        "plan_id": str(itinerary.id),
        "plan_type": itinerary.plan_type,
        "status": itinerary.status,
        "total_cost": money(itinerary.total_cost),
        "total_minutes": itinerary.total_minutes,
        "transport_minutes": sum(i.transport_minutes or 0 for i in items),
        "items": [to_item_dto(i) for i in items],
        "request_snapshot": itinerary.request_snapshot or {},
        "explanation": "",
        "highlights": PLAN_HIGHLIGHTS.get(itinerary.plan_type, []),
        "suited_for": PLAN_SUITED_FOR.get(itinerary.plan_type, ""),
        "solver_note": itinerary.solver_note,
        "data_time": itinerary.data_time.isoformat() if itinerary.data_time else None,
        "version_lineage": itinerary.version_lineage,
    }


def to_plan_summary(itinerary: Itinerary, items: list[ItineraryItem]) -> dict:
    return {
        "plan_id": str(itinerary.id),
        "plan_type": itinerary.plan_type,
        "status": itinerary.status,
        "total_cost": money(itinerary.total_cost),
        "total_minutes": itinerary.total_minutes,
        "transport_minutes": sum(i.transport_minutes or 0 for i in items),
        "highlights": PLAN_HIGHLIGHTS.get(itinerary.plan_type, []),
        "suited_for": PLAN_SUITED_FOR.get(itinerary.plan_type, ""),
    }


class ItineraryService:
    def __init__(
        self,
        itin_repo: ItineraryRepository,
        item_repo: ItineraryItemRepository,
        event_repo: ChangeEventRepository,
    ) -> None:
        self.itin_repo = itin_repo
        self.item_repo = item_repo
        self.event_repo = event_repo

    async def next_lineage(self, conversation_id: int) -> int:
        existing = await self.itin_repo.list_by_conversation(conversation_id)
        lineages = [i.version_lineage for i in existing if i.version_lineage is not None]
        return (max(lineages) + 1) if lineages else 1

    async def load_plan(self, conversation_id: int, plan_id: int) -> Itinerary:
        itinerary = await self.itin_repo.get_for_conversation(conversation_id, plan_id)
        if itinerary is None:
            raise BizError(
                ErrorCode.NOT_FOUND,
                "行程不存在或不属于当前会话",
                plan_id=str(plan_id),
            )
        return itinerary

    async def save_draft_plans(
        self,
        conversation_id: int,
        plans: list[ItineraryPlan],
        request_snapshot: dict,
        data_time: datetime | None = None,
    ) -> list[Itinerary]:
        if not plans:
            return []
        lineage = await self.next_lineage(conversation_id)
        existing = await self.itin_repo.list_by_conversation(conversation_id)
        for old in existing:
            if old.status == DRAFT:
                old.status = REPLACED

        saved: list[Itinerary] = []
        for plan in plans:
            plan_type = GOAL_TO_TYPE.get(plan.goal.value, "均衡")
            itinerary = Itinerary(
                conversation_id=conversation_id,
                plan_type=plan_type,
                status=DRAFT,
                total_cost=round(plan.total_cost, 2),
                total_minutes=max(plan.total_minutes, 1),
                request_snapshot=request_snapshot,
                solver_note=plan.solver_note,
                data_time=data_time,
                version_lineage=lineage,
            )
            itinerary = await self.itin_repo.add(itinerary)
            new_items = plan_to_items(itinerary.id, plan, request_snapshot)
            if new_items:
                await self.item_repo.bulk_add(new_items)
            saved.append(itinerary)
        return saved

    async def persist_pending_plan(
        self,
        conversation_id: int,
        original: Itinerary,
        new_plan: ItineraryPlan,
        request_snapshot: dict,
        data_time: datetime | None = None,
    ) -> Itinerary:
        existing = await self.itin_repo.list_by_conversation(conversation_id)
        for old in existing:
            if old.status == PENDING and old.version_lineage == original.version_lineage:
                old.status = REPLACED
        itinerary = Itinerary(
            conversation_id=conversation_id,
            plan_type=original.plan_type,
            status=PENDING,
            total_cost=round(new_plan.total_cost, 2),
            total_minutes=max(new_plan.total_minutes, 1),
            request_snapshot=request_snapshot,
            solver_note=new_plan.solver_note,
            data_time=data_time,
            version_lineage=original.version_lineage,
        )
        itinerary = await self.itin_repo.add(itinerary)
        new_items = plan_to_items(itinerary.id, new_plan, request_snapshot)
        if new_items:
            await self.item_repo.bulk_add(new_items)
        await self.event_repo.append(
            event_type="用户调整",
            entity_type="itinerary",
            entity_id=original.id,
            payload={
                "action": "replan_suggestion",
                "new_itinerary_id": itinerary.id,
                "total_cost": money(new_plan.total_cost),
            },
            affected_itinerary_ids=[original.id],
        )
        return itinerary

    async def confirm_plan(self, conversation_id: int, plan_id: int) -> Itinerary:
        itinerary = await self.load_plan(conversation_id, plan_id)
        if itinerary.status not in (DRAFT, PENDING):
            raise BizError(
                ErrorCode.STATE_CONFLICT,
                f"方案当前状态为「{itinerary.status}」，仅草稿或挂起建议可确认",
                plan_id=str(plan_id),
            )
        previous_status = itinerary.status
        itinerary.status = CONFIRMED
        siblings = await self.itin_repo.list_by_conversation(conversation_id)
        replaced_ids: list[int] = []
        for sib in siblings:
            if sib.id == itinerary.id:
                continue
            if sib.status == CONFIRMED and sib.version_lineage == itinerary.version_lineage:
                sib.status = REPLACED
                replaced_ids.append(sib.id)
        await self.event_repo.append(
            event_type="用户调整",
            entity_type="itinerary",
            entity_id=itinerary.id,
            payload={
                "action": "confirm",
                "from_status": previous_status,
                "replaced_itinerary_ids": replaced_ids,
                "total_cost": money(itinerary.total_cost),
            },
            affected_itinerary_ids=[itinerary.id, *replaced_ids],
        )
        return itinerary

    async def list_pending_suggestions(self, conversation_id: int) -> list[Itinerary]:
        existing = await self.itin_repo.list_by_conversation(conversation_id)
        return [i for i in existing if i.status == PENDING]

    async def get_detail(self, conversation_id: int, plan_id: int) -> dict:
        itinerary = await self.load_plan(conversation_id, plan_id)
        items = await self.item_repo.list_by_itinerary(itinerary.id)
        detail = to_plan_detail(itinerary, items)
        detail["explanation"] = self._build_explanation(itinerary, items)
        return detail

    def _build_explanation(self, itinerary: Itinerary, items: list[ItineraryItem]) -> str:
        sights = [i for i in items if i.activity_type == "游览"]
        names = "、".join(
            [f"POI-{i.poi_id}" for i in sights[:3]] if sights else []
        )
        return (
            f"本方案为{itinerary.plan_type}型行程，共 {len(sights)} 个游览项目，"
            f"预计总花费 {money(itinerary.total_cost)} 元，总时长 {itinerary.total_minutes // 60} 小时。"
        )