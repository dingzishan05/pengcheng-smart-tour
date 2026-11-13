from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.core.errors import BizError, ErrorCode
from app.models import PriceRecord
from app.services.audit_service import AuditService
from app.services.price_service import (
    REVIEW_APPROVED,
    REVIEW_PENDING,
    REVIEW_REJECTED,
    PriceService,
    resolve_source_priority,
    to_price_dto,
)


class FakePriceRepo:
    def __init__(self) -> None:
        self.data: list[PriceRecord] = []
        self._next_id = 10

    async def add(self, record: PriceRecord) -> PriceRecord:
        record.id = self._next_id
        self._next_id += 1
        self.data.append(record)
        return record

    async def get(self, record_id: int):
        return next((r for r in self.data if r.id == record_id), None)

    async def get_current(self, poi_id: int, price_type: str = "门票"):
        return next(
            (
                r
                for r in self.data
                if r.poi_id == poi_id
                and r.price_type == price_type
                and r.is_current
                and r.review_status == REVIEW_APPROVED
            ),
            None,
        )

    async def list_history(self, poi_id: int, price_type: str | None = None):
        rows = [r for r in self.data if r.poi_id == poi_id]
        if price_type is not None:
            rows = [r for r in rows if r.price_type == price_type]
        return sorted(rows, key=lambda r: r.id, reverse=True)


class FakeAuditRepo:
    def __init__(self) -> None:
        self.logs: list[dict] = []

    async def append(self, **kwargs):
        self.logs.append(kwargs)
        return kwargs


def make_service(affected=None):
    price_repo = FakePriceRepo()
    audit_repo = FakeAuditRepo()
    audit_service = AuditService(audit_repo)

    async def default_affected(poi_id):
        return []

    service = PriceService(
        price_repo,
        audit_service,
        affected_finder=affected or default_affected,
    )
    return service, price_repo, audit_repo


def make_record(poi_id=1, amount=60, channel="官方渠道", current=False, status=REVIEW_PENDING):
    return PriceRecord(
        poi_id=poi_id,
        price_type="门票",
        amount=amount,
        source_channel=channel,
        source_priority=resolve_source_priority(channel),
        collected_at=date.today(),
        valid_until=date.today() + timedelta(days=30),
        review_status=status,
        is_current=current,
    )


async def event_spy():
    events: list[dict] = []

    async def appender(**kwargs):
        events.append(kwargs)

    return appender, events


async def test_resolve_source_priority():
    assert resolve_source_priority("官方公告") == 1
    assert resolve_source_priority("实地采集") == 2
    assert resolve_source_priority("游客上报") == 3
    assert resolve_source_priority("未知") == 3


async def test_submit_creates_pending_record_with_audit():
    service, price_repo, audit_repo = make_service()
    record = await service.submit(
        poi_id=1,
        price_type="门票",
        amount=60,
        source_channel="实地采集",
        collected_at=date.today(),
        valid_until=date.today() + timedelta(days=30),
        operator_id=2,
    )
    assert record.review_status == REVIEW_PENDING
    assert record.is_current is False
    assert record.source_priority == 2
    assert len(audit_repo.logs) == 1
    assert audit_repo.logs[0]["action"] == "price_submit"


async def test_submit_rejects_invalid_validity():
    service, _, _ = make_service()
    with pytest.raises(BizError) as exc_info:
        await service.submit(
            poi_id=1,
            price_type="门票",
            amount=60,
            source_channel="官方",
            collected_at=date.today(),
            valid_until=date.today(),
            operator_id=2,
        )
    assert exc_info.value.code == ErrorCode.PARAM_INVALID


async def test_review_approve_takes_effect_atomically():
    service, price_repo, audit_repo = make_service()
    record = await service.submit(
        poi_id=1,
        price_type="门票",
        amount=60,
        source_channel="官方",
        collected_at=date.today(),
        valid_until=date.today() + timedelta(days=90),
        operator_id=2,
    )

    appender, events = await event_spy()

    async def affected(poi_id):
        return [11, 12]

    service.affected_finder = affected
    service_event = PriceService(price_repo, service.audit_service, affected_finder=affected)
    outcome = await service_event.review(
        record_id=record.id, action="approve", reviewer_id=3, event_appender=appender
    )

    assert outcome["took_effect"] is True
    assert record.review_status == REVIEW_APPROVED
    assert record.is_current is True
    assert outcome["affected_itinerary_ids"] == [11, 12]
    assert len(events) == 1
    assert events[0]["event_type"] == "价格变更"
    assert events[0]["affected_itinerary_ids"] == [11, 12]
    assert len(audit_repo.logs) == 2
    assert audit_repo.logs[-1]["action"] == "price_review_approve"
    assert audit_repo.logs[-1]["before_value"]["review_status"] == REVIEW_PENDING
    assert audit_repo.logs[-1]["after_value"]["is_current"] is True


async def test_review_reject_does_not_affect_current():
    service, price_repo, _ = make_service()
    current = make_record(poi_id=1, amount=60, channel="官方", current=True, status=REVIEW_APPROVED)
    await price_repo.add(current)
    pending = make_record(poi_id=1, amount=99, channel="上报")
    await price_repo.add(pending)

    outcome = await service.review(record_id=pending.id, action="reject", reviewer_id=3, remark="价格不合理")
    assert outcome["took_effect"] is False
    assert pending.review_status == REVIEW_REJECTED
    assert pending.conflict_note == "价格不合理"
    assert current.is_current is True


async def test_review_twice_conflicts():
    service, price_repo, _ = make_service()
    record = make_record(poi_id=1, amount=60, channel="官方", status=REVIEW_APPROVED)
    await price_repo.add(record)
    with pytest.raises(BizError) as exc_info:
        await service.review(record_id=record.id, action="approve", reviewer_id=3)
    assert exc_info.value.code == ErrorCode.STATE_CONFLICT


async def test_review_lower_priority_does_not_override_current():
    service, price_repo, _ = make_service()
    current = make_record(poi_id=1, amount=60, channel="官方渠道", current=True, status=REVIEW_APPROVED)
    await price_repo.add(current)
    late = make_record(poi_id=1, amount=50, channel="游客上报", status=REVIEW_PENDING)
    await price_repo.add(late)

    outcome = await service.review(record_id=late.id, action="approve", reviewer_id=3)
    assert outcome["took_effect"] is False
    assert late.review_status == REVIEW_APPROVED
    assert late.is_current is False
    assert late.conflict_note is not None
    assert current.is_current is True


async def test_review_same_priority_later_overrides_current():
    service, price_repo, _ = make_service()
    current = make_record(poi_id=1, amount=60, channel="实地采集", current=True, status=REVIEW_APPROVED)
    await price_repo.add(current)
    late = make_record(poi_id=1, amount=55, channel="实地补采", status=REVIEW_PENDING)
    await price_repo.add(late)

    outcome = await service.review(record_id=late.id, action="approve", reviewer_id=3)
    assert outcome["took_effect"] is True
    assert late.is_current is True
    assert current.is_current is False
    assert current.conflict_note is not None


async def test_review_not_found():
    service, _, _ = make_service()
    with pytest.raises(BizError) as exc_info:
        await service.review(record_id=999, action="approve", reviewer_id=3)
    assert exc_info.value.code == ErrorCode.NOT_FOUND


async def test_visitor_view_hides_pending_and_rejected():
    service, price_repo, _ = make_service()
    current = make_record(poi_id=1, amount=60, channel="官方", current=True, status=REVIEW_APPROVED)
    pending = make_record(poi_id=1, amount=70, channel="上报", status=REVIEW_PENDING)
    rejected = make_record(poi_id=1, amount=80, channel="上报", status=REVIEW_REJECTED)
    for record in (current, pending, rejected):
        await price_repo.add(record)

    view = await service.get_view(1, include_hidden=False)
    assert view["current"]["record_id"] == str(current.id)
    assert len(view["records"]) == 1
    assert view["records"][0]["review_status"] == REVIEW_APPROVED

    full = await service.get_view(1, include_hidden=True)
    assert len(full["records"]) == 3
    assert to_price_dto(current)["amount"] == "60.00"