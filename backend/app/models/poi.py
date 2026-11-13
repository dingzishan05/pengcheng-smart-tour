from datetime import date, datetime, time

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel

XUZHOU_LON_MIN, XUZHOU_LON_MAX = 116.9, 117.6
XUZHOU_LAT_MIN, XUZHOU_LAT_MAX = 34.1, 34.4


class Poi(BaseModel):
    __tablename__ = "pois"
    __table_args__ = (
        CheckConstraint(
            "category IN ('景点','博物馆','餐饮','商圈','交通枢纽')", name="ck_poi_category"
        ),
        CheckConstraint("cardinality(tags) >= 1", name="ck_poi_tags_not_empty"),
        CheckConstraint(
            f"longitude BETWEEN {XUZHOU_LON_MIN} AND {XUZHOU_LON_MAX}", name="ck_poi_lon_range"
        ),
        CheckConstraint(
            f"latitude BETWEEN {XUZHOU_LAT_MIN} AND {XUZHOU_LAT_MAX}", name="ck_poi_lat_range"
        ),
        CheckConstraint("visit_minutes > 0", name="ck_poi_visit_minutes"),
        CheckConstraint("status IN ('有效','失效')", name="ck_poi_status"),
        Index("idx_pois_status", "status"),
        Index("idx_pois_tags_gin", "tags", postgresql_using="gin"),
    )

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(20)), nullable=False)
    longitude: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    latitude: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    city: Mapped[str] = mapped_column(String(50), nullable=False, default="徐州市")
    visit_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    indoor: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="有效")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_channel: Mapped[str] = mapped_column(String(50), nullable=False)
    collected_at: Mapped[date] = mapped_column(Date, nullable=False)


class PoiHours(BaseModel):
    __tablename__ = "poi_hours"
    __table_args__ = (
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_poi_hours_dow"),
        CheckConstraint("valid_until > collected_at", name="ck_poi_hours_validity"),
        UniqueConstraint("poi_id", "day_of_week", name="uq_poi_hours_poi_dow"),
        Index("idx_poi_hours_poi", "poi_id"),
    )

    poi_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("pois.id"), nullable=False)
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    open_time: Mapped[time | None] = mapped_column(nullable=True)
    close_time: Mapped[time | None] = mapped_column(nullable=True)
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_channel: Mapped[str] = mapped_column(String(50), nullable=False)
    collected_at: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)


class PriceRecord(BaseModel):
    __tablename__ = "price_records"
    __table_args__ = (
        CheckConstraint("price_type IN ('门票','交通','餐饮')", name="ck_price_type"),
        CheckConstraint("amount >= 0", name="ck_price_amount"),
        CheckConstraint("valid_until > collected_at", name="ck_price_validity"),
        CheckConstraint(
            "review_status IN ('待审核','已通过','已驳回')", name="ck_price_review_status"
        ),
        Index(
            "uq_price_current",
            "poi_id",
            "price_type",
            unique=True,
            postgresql_where=text("is_current = true AND review_status = '已通过'"),
        ),
        Index("idx_price_review_queue", "poi_id", "price_type", "review_status"),
    )

    poi_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("pois.id"), nullable=False)
    price_type: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    source_channel: Mapped[str] = mapped_column(String(50), nullable=False)
    source_priority: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    collected_at: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    review_status: Mapped[str] = mapped_column(String(10), nullable=False, default="待审核")
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reviewed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    conflict_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class TransportRoute(BaseModel):
    __tablename__ = "transport_routes"
    __table_args__ = (
        CheckConstraint("mode IN ('公交','步行','驾车')", name="ck_route_mode"),
    )

    origin_poi_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pois.id"), primary_key=True, autoincrement=False
    )
    dest_poi_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pois.id"), primary_key=True, autoincrement=False
    )
    mode: Mapped[str] = mapped_column(String(10), primary_key=True)
    duration_minutes: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    distance_meters: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_channel: Mapped[str] = mapped_column(String(50), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)