from __future__ import annotations

from datetime import date

from app.core.errors import BizError, ErrorCode
from app.models import PriceRecord
from app.repositories.poi_repo import PriceRepository
from app.services.audit_service import AuditService
from app.services.itinerary_service import money

SOURCE_PRIORITY = {"官方": 1, "实地": 2, "上报": 3}
REVIEW_PENDING = "待审核"
REVIEW_APPROVED = "已通过"
REVIEW_REJECTED = "已驳回"

VISITOR_HIDDEN = (REVIEW_PENDING, REVIEW_REJECTED)


def resolve_source_priority(channel: str) -> int:
    for keyword, priority in SOURCE_PRIORITY.items():
        if keyword in channel:
            return priority
    return SOURCE_PRIORITY["上报"]


def to_price_dto(record: PriceRecord) -> dict:
    return {
        "record_id": str(record.id),
        "poi_id": str(record.poi_id),
        "price_type": record.price_type,
        "amount": money(record.amount),
        "source_channel": record.source_channel,
        "collected_at": record.collected_at.isoformat(),
        "valid_until": record.valid_until.isoformat(),
        "review_status": record.review_status,
        "is_current": record.is_current,
        "conflict_note": record.conflict_note,
    }


class PriceService:
    def __init__(
        self,
        price_repo: PriceRepository,
        audit_service: AuditService,
        affected_finder=None,
    ) -> None:
        self.price_repo = price_repo
        self.audit_service = audit_service
        self.affected_finder = affected_finder

    async def get_view(self, poi_id: int, include_hidden: bool = False) -> dict:
        records = await self.price_repo.list_history(poi_id)
        current = next(
            (r for r in records if r.is_current and r.review_status == REVIEW_APPROVED),
            None,
        )
        visible = [
            r for r in records
            if include_hidden or r.review_status not in VISITOR_HIDDEN
        ]
        return {
            "current": to_price_dto(current) if current is not None else None,
            "records": [to_price_dto(r) for r in visible],
        }

    async def submit(
        self,
        *,
        poi_id: int,
        price_type: str,
        amount: float,
        source_channel: str,
        collected_at: date,
        valid_until: date,
        operator_id: int,
    ) -> PriceRecord:
        if amount < 0:
            raise BizError(ErrorCode.PARAM_INVALID, "价格不能为负数")
        if valid_until <= collected_at:
            raise BizError(ErrorCode.PARAM_INVALID, "有效期必须晚于采集日期")
        record = PriceRecord(
            poi_id=poi_id,
            price_type=price_type,
            amount=round(float(amount), 2),
            source_channel=source_channel,
            source_priority=resolve_source_priority(source_channel),
            collected_at=collected_at,
            valid_until=valid_until,
            review_status=REVIEW_PENDING,
            is_current=False,
        )
        record = await self.price_repo.add(record)
        await self.audit_service.log(
            operator_id=operator_id,
            action="price_submit",
            entity_type="price_record",
            entity_id=record.id,
            after_value=to_price_dto(record),
        )
        return record

    async def review(
        self,
        *,
        record_id: int,
        action: str,
        reviewer_id: int,
        remark: str | None = None,
        event_appender=None,
    ) -> dict:
        if action not in ("approve", "reject"):
            raise BizError(ErrorCode.PARAM_INVALID, "action 必须是 approve 或 reject")
        record = await self.price_repo.get(record_id)
        if record is None:
            raise BizError(ErrorCode.NOT_FOUND, "价格记录不存在")
        if record.review_status != REVIEW_PENDING:
            raise BizError(
                ErrorCode.STATE_CONFLICT,
                f"该记录已处于「{record.review_status}」状态，不可重复审核",
            )

        before = to_price_dto(record)
        if action == "reject":
            record.review_status = REVIEW_REJECTED
            record.reviewed_by = reviewer_id
            record.conflict_note = remark or "审核驳回"
            await self.audit_service.log(
                operator_id=reviewer_id,
                action="price_review_reject",
                entity_type="price_record",
                entity_id=record.id,
                before_value=before,
                after_value=to_price_dto(record),
            )
            return {"record": to_price_dto(record), "took_effect": False}

        record.review_status = REVIEW_APPROVED
        record.reviewed_by = reviewer_id

        current = await self.price_repo.get_current(record.poi_id, record.price_type)
        took_effect = True
        if current is not None and current.id != record.id:
            if current.source_priority < record.source_priority:
                took_effect = False
                record.is_current = False
                record.conflict_note = (
                    f"现行价来源「{current.source_channel}」优先级更高，本记录落选保留备查"
                )
            else:
                current.is_current = False
                current.conflict_note = "被后审核通过且优先级更高的记录覆盖"
        if took_effect:
            record.is_current = True

        affected_ids: list[int] = []
        if took_effect and self.affected_finder is not None:
            affected_ids = list(await self.affected_finder(record.poi_id))
        if took_effect and event_appender is not None:
            await event_appender(
                event_type="价格变更",
                entity_type="poi",
                entity_id=record.poi_id,
                payload={
                    "price_type": record.price_type,
                    "new_amount": money(record.amount),
                    "source_channel": record.source_channel,
                },
                affected_itinerary_ids=affected_ids,
            )
        await self.audit_service.log(
            operator_id=reviewer_id,
            action="price_review_approve",
            entity_type="price_record",
            entity_id=record.id,
            before_value=before,
            after_value=to_price_dto(record),
        )
        return {
            "record": to_price_dto(record),
            "took_effect": took_effect,
            "affected_itinerary_ids": affected_ids,
        }