from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.keys import poi_active_key, price_key
from app.cache.service import cache_get, cache_set
from app.core.logging import get_logger
from app.models import Poi, PoiHours, PriceRecord
from app.repositories.poi_repo import PoiHoursRepository, PoiRepository, PriceRepository
from app.tools.base import ToolErrorKind, ToolMeta, ToolResult

logger = get_logger("app.tools.pois")

POI_CACHE_TTL = 6 * 3600


class Coordinate(BaseModel):
    lat: float
    lng: float


class TimeWindow(BaseModel):
    open_minute: int
    close_minute: int


class PoiQuery(BaseModel):
    tags: list[str] = Field(default_factory=list)
    day_of_week: int = Field(ge=0, le=6)
    budget_cap: Decimal = Decimal("0")
    center: Coordinate | None = None
    radius_km: float | None = None
    short_trip: bool = False


class PoiCandidate(BaseModel):
    poi_id: int
    name: str
    category: str
    tags: list[str]
    location: Coordinate
    visit_minutes: int
    ticket_price: float = 0
    open_window: TimeWindow | None = None
    closed_today: bool = False
    interest_score: float = 0.5
    indoor: bool = True
    source_channel: str = ""
    collected_at: datetime | None = None


class HoursInfo(BaseModel):
    windows: list[TimeWindow] = Field(default_factory=list)
    closed_today: bool = False
    source_channel: str = ""
    collected_at: datetime | None = None
    is_reference: bool = False


class PriceInfo(BaseModel):
    amount: float
    source_channel: str
    collected_at: datetime | None = None
    valid_until: datetime | None = None
    freshness: str = "fresh"
    is_reference: bool = False


def _minutes(value: time | None) -> int | None:
    return value.hour * 60 + value.minute if value else None



def _distance_km(a: Coordinate, b: Coordinate) -> float:
    from app.optimizer.models import haversine_km

    return haversine_km(a.lat, a.lng, b.lat, b.lng)


async def _load_active_pois(session: AsyncSession) -> list[dict]:
    key = poi_active_key()
    hit = await cache_get(key)
    if hit is not None:
        return hit
    repo = PoiRepository(session)
    pois = await repo.list_active()
    payload = [
        {
            "poi_id": p.id,
            "name": p.name,
            "category": p.category,
            "tags": list(p.tags),
            "lat": float(p.latitude),
            "lng": float(p.longitude),
            "visit_minutes": p.visit_minutes,
            "indoor": p.indoor,
            "source_channel": p.source_channel,
            "collected_at": p.collected_at.isoformat() if p.collected_at else None,
        }
        for p in pois
    ]
    await cache_set(key, payload, POI_CACHE_TTL)
    return payload


async def search_attractions(session: AsyncSession, query: PoiQuery) -> ToolResult[list[PoiCandidate]]:
    try:
        pois = await _load_active_pois(session)
        hours_repo = PoiHoursRepository(session)
        price_repo = PriceRepository(session)

        candidates: list[PoiCandidate] = []
        for poi in pois:
            location = Coordinate(lat=poi["lat"], lng=poi["lng"])
            if query.center is not None and query.radius_km is not None:
                if _distance_km(query.center, location) > query.radius_km:
                    continue

            hours = await hours_repo.get_hours(poi["poi_id"], _fake_date(query.day_of_week))
            if hours is not None and hours.closed:
                continue
            window = None
            if hours is not None and hours.open_time and hours.close_time:
                open_m = _minutes(hours.open_time)
                close_m = _minutes(hours.close_time)
                if open_m is not None and close_m is not None:
                    window = TimeWindow(open_minute=open_m, close_minute=close_m)

            ticket_price = 0.0
            price = await price_repo.get_current(poi["poi_id"])
            if price is not None:
                ticket_price = float(price.amount)
                if query.budget_cap and ticket_price > float(query.budget_cap):
                    continue

            candidates.append(
                PoiCandidate(
                    poi_id=poi["poi_id"],
                    name=poi["name"],
                    category=poi["category"],
                    tags=poi["tags"],
                    location=location,
                    visit_minutes=poi["visit_minutes"],
                    ticket_price=ticket_price,
                    open_window=window,
                    interest_score=_interest_score_from(poi["tags"], query.tags),
                    indoor=poi["indoor"],
                    source_channel=poi["source_channel"],
                    collected_at=datetime.fromisoformat(poi["collected_at"]) if poi["collected_at"] else None,
                )
            )
        return ToolResult[list[PoiCandidate]].success(
            candidates,
            ToolMeta(source_channel="local_db", collected_at=datetime.now(timezone.utc)),
        )
    except Exception as exc:
        logger.error("search_attractions failed", extra={"kind": "external", "error": str(exc)[:200]})
        return ToolResult.failure(ToolErrorKind.EXTERNAL, "50005", f"POI 检索失败: {exc.__class__.__name__}")


def _fake_date(day_of_week: int) -> date:
    base = date(2026, 1, 5)
    return base.fromordinal(base.toordinal() + day_of_week)


def _interest_score_from(poi_tags: list[str], query_tags: list[str]) -> float:
    if not query_tags:
        return 0.5
    hits = len(set(poi_tags) & set(query_tags))
    if hits == 0:
        return 0.3
    return min(1.0, 0.5 + 0.25 * hits)


async def get_opening_hours(session: AsyncSession, poi_id: int, visit_date: date) -> ToolResult[HoursInfo]:
    try:
        repo = PoiHoursRepository(session)
        hours = await repo.get_hours(poi_id, visit_date)
        if hours is None:
            return ToolResult.success(
                HoursInfo(is_reference=True, closed_today=True),
                ToolMeta(source_channel="local_db", degraded=True),
            )
        windows: list[TimeWindow] = []
        if hours.open_time and hours.close_time:
            open_m = _minutes(hours.open_time)
            close_m = _minutes(hours.close_time)
            if open_m is not None and close_m is not None:
                windows.append(TimeWindow(open_minute=open_m, close_minute=close_m))
        info = HoursInfo(
            windows=windows,
            closed_today=hours.closed,
            source_channel=hours.source_channel,
            collected_at=datetime.combine(hours.collected_at, time.min) if hours.collected_at else None,
            is_reference=hours.valid_until < date.today(),
        )
        return ToolResult.success(info, ToolMeta(source_channel=hours.source_channel))
    except Exception as exc:
        logger.error("get_opening_hours failed", extra={"kind": "external", "error": str(exc)[:200]})
        return ToolResult.failure(ToolErrorKind.EXTERNAL, "50005", f"开放时间查询失败: {exc.__class__.__name__}")


async def get_ticket_price(session: AsyncSession, poi_id: int, visit_date: date | None = None) -> ToolResult[PriceInfo]:
    try:
        key = price_key(poi_id)
        hit = await cache_get(key)
        if hit is not None:
            return ToolResult.success(PriceInfo.model_validate(hit))
        repo = PriceRepository(session)
        record = await repo.get_current(poi_id)
        if record is None:
            return ToolResult.failure(
                ToolErrorKind.DATA, "40401", f"POI {poi_id} 无已通过审核的现行价格"
            )
        now = date.today()
        stale = record.valid_until < now
        info = PriceInfo(
            amount=float(record.amount),
            source_channel=record.source_channel,
            collected_at=datetime.combine(record.collected_at, time.min),
            valid_until=datetime.combine(record.valid_until, time.min),
            freshness="stale" if stale else "fresh",
            is_reference=stale,
        )
        await cache_set(key, info.model_dump(), max(0, int((record.valid_until - now).total_seconds())) or 3600)
        return ToolResult.success(info, ToolMeta(source_channel=record.source_channel, degraded=stale))
    except Exception as exc:
        logger.error("get_ticket_price failed", extra={"kind": "external", "error": str(exc)[:200]})
        return ToolResult.failure(ToolErrorKind.EXTERNAL, "50005", f"价格查询失败: {exc.__class__.__name__}")