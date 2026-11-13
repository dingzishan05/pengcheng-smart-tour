from datetime import date

from sqlalchemy import func, select

from app.models import Poi, PoiHours, PriceRecord, TransportRoute
from app.repositories.base import BaseRepository


class PoiRepository(BaseRepository[Poi]):
    def __init__(self, session) -> None:
        super().__init__(session, Poi)

    async def list_active(
        self, category: str | None = None, tag: str | None = None
    ) -> list[Poi]:
        stmt = select(Poi).where(Poi.status == "有效")
        if category is not None:
            stmt = stmt.where(Poi.category == category)
        if tag is not None:
            stmt = stmt.where(Poi.tags.contains([tag]))
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_paged(
        self,
        category: str | None = None,
        tag: str | None = None,
        status: str | None = "有效",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Poi], int]:
        conditions = []
        if status is not None:
            conditions.append(Poi.status == status)
        if category is not None:
            conditions.append(Poi.category == category)
        if tag is not None:
            conditions.append(Poi.tags.contains([tag]))
        base = select(Poi)
        if conditions:
            base = base.where(*conditions)
        total = (await self.session.execute(
            select(func.count()).select_from(base.subquery())
        )).scalar_one()
        rows = (
            await self.session.execute(
                base.order_by(Poi.category.asc(), Poi.id.asc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
        return list(rows), int(total)

    async def get_by_name(self, name: str) -> Poi | None:
        stmt = select(Poi).where(Poi.name == name)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> list[Poi]:
        return list((await self.session.execute(select(Poi))).scalars().all())


class PoiHoursRepository(BaseRepository[PoiHours]):
    def __init__(self, session) -> None:
        super().__init__(session, PoiHours)

    async def get_hours(self, poi_id: int, visit_date: date) -> PoiHours | None:
        stmt = select(PoiHours).where(
            PoiHours.poi_id == poi_id, PoiHours.day_of_week == visit_date.weekday()
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_week(self, poi_id: int) -> list[PoiHours]:
        stmt = (
            select(PoiHours)
            .where(PoiHours.poi_id == poi_id)
            .order_by(PoiHours.day_of_week.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_expired(self, today: date) -> list[PoiHours]:
        stmt = select(PoiHours).where(PoiHours.valid_until < today)
        return list((await self.session.execute(stmt)).scalars().all())


class PriceRepository(BaseRepository[PriceRecord]):
    def __init__(self, session) -> None:
        super().__init__(session, PriceRecord)

    async def get_current(self, poi_id: int, price_type: str = "门票") -> PriceRecord | None:
        stmt = select(PriceRecord).where(
            PriceRecord.poi_id == poi_id,
            PriceRecord.price_type == price_type,
            PriceRecord.is_current.is_(True),
            PriceRecord.review_status == "已通过",
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_pending(self, limit: int = 100) -> list[PriceRecord]:
        stmt = (
            select(PriceRecord)
            .where(PriceRecord.review_status == "待审核")
            .order_by(PriceRecord.created_at.asc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_expired_current(self, today: date) -> list[PriceRecord]:
        stmt = select(PriceRecord).where(
            PriceRecord.is_current.is_(True),
            PriceRecord.review_status == "已通过",
            PriceRecord.valid_until < today,
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_history(self, poi_id: int, price_type: str | None = None) -> list[PriceRecord]:
        stmt = (
            select(PriceRecord)
            .where(PriceRecord.poi_id == poi_id)
            .order_by(PriceRecord.collected_at.desc(), PriceRecord.id.desc())
        )
        if price_type is not None:
            stmt = stmt.where(PriceRecord.price_type == price_type)
        return list((await self.session.execute(stmt)).scalars().all())


class TransportRouteRepository(BaseRepository[TransportRoute]):
    def __init__(self, session) -> None:
        super().__init__(session, TransportRoute)

    async def get_route(self, origin_id: int, dest_id: int, mode: str) -> TransportRoute | None:
        stmt = select(TransportRoute).where(
            TransportRoute.origin_poi_id == origin_id,
            TransportRoute.dest_poi_id == dest_id,
            TransportRoute.mode == mode,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> list[TransportRoute]:
        return list((await self.session.execute(select(TransportRoute))).scalars().all())

    async def upsert_route(self, route: TransportRoute) -> TransportRoute:
        merged = await self.session.merge(route)
        await self.session.flush()
        return merged