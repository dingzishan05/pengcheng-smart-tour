from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class Itinerary(BaseModel):
    __tablename__ = "itineraries"
    __table_args__ = (
        CheckConstraint("plan_type IN ('省钱','省时','均衡')", name="ck_itinerary_plan_type"),
        CheckConstraint(
            "status IN ('草稿','已确认','已被替换','挂起建议')", name="ck_itinerary_status"
        ),
        CheckConstraint("total_cost >= 0", name="ck_itinerary_total_cost"),
        CheckConstraint("total_minutes > 0", name="ck_itinerary_total_minutes"),
        Index("idx_itineraries_conversation_status", "conversation_id", "status"),
    )

    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id"), nullable=False
    )
    plan_type: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="草稿")
    total_cost: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    total_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    request_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    solver_note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    data_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version_lineage: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class ItineraryItem(BaseModel):
    __tablename__ = "itinerary_items"
    __table_args__ = (
        CheckConstraint("activity_type IN ('游览','用餐','交通')", name="ck_item_activity_type"),
        CheckConstraint("end_at > start_at", name="ck_item_time_order"),
        CheckConstraint("estimated_cost >= 0", name="ck_item_cost"),
        UniqueConstraint("itinerary_id", "seq", name="uq_item_seq"),
        Index("idx_items_itinerary_seq", "itinerary_id", "seq"),
    )

    itinerary_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("itineraries.id"), nullable=False
    )
    seq: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    poi_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("pois.id"), nullable=True)
    activity_type: Mapped[str] = mapped_column(String(10), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    transport_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    transport_minutes: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    estimated_cost: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    cost_meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class ChangeEvent(BaseModel):
    __tablename__ = "change_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('用户调整','价格变更','时间变更','POI状态变更','天气异常')",
            name="ck_change_event_type",
        ),
        Index("idx_change_events_entity", "entity_type", "entity_id", "occurred_at"),
        Index("idx_change_events_affected_gin", "affected_itinerary_ids", postgresql_using="gin"),
    )

    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    affected_itinerary_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False, default=list
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.now
    )