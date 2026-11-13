from datetime import datetime

from sqlalchemy import distinct, func, select

from app.models import ChangeEvent, Itinerary, ItineraryItem
from app.repositories.base import BaseRepository


class ItineraryRepository(BaseRepository[Itinerary]):
    def __init__(self, session) -> None:
        super().__init__(session, Itinerary)

    async def list_referencing(self, poi_id: int) -> list[Itinerary]:
        stmt = (
            select(Itinerary)
            .join(ItineraryItem, ItineraryItem.itinerary_id == Itinerary.id)
            .where(
                ItineraryItem.poi_id == poi_id,
                Itinerary.status.in_(["已确认", "草稿", "挂起建议"]),
            )
        )
        return list((await self.session.execute(stmt)).unique().scalars().all())

    async def list_by_conversation(self, conversation_id: int) -> list[Itinerary]:
        stmt = (
            select(Itinerary)
            .where(Itinerary.conversation_id == conversation_id)
            .order_by(Itinerary.id.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_for_conversation(
        self, conversation_id: int, itinerary_id: int
    ) -> Itinerary | None:
        stmt = select(Itinerary).where(
            Itinerary.id == itinerary_id,
            Itinerary.conversation_id == conversation_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_affected(self, itinerary_ids: list[int]) -> list[Itinerary]:
        if not itinerary_ids:
            return []
        stmt = select(Itinerary).where(
            Itinerary.id.in_(itinerary_ids),
            Itinerary.status.in_(["已确认", "草稿", "挂起建议"]),
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_confirmed(self) -> list[Itinerary]:
        stmt = select(Itinerary).where(Itinerary.status == "已确认")
        return list((await self.session.execute(stmt)).scalars().all())

    async def count_pending_by_conversations(self, conversation_ids: list[int]) -> dict[int, int]:
        if not conversation_ids:
            return {}
        stmt = (
            select(Itinerary.conversation_id, func.count(Itinerary.id))
            .where(
                Itinerary.conversation_id.in_(conversation_ids),
                Itinerary.status == "挂起建议",
            )
            .group_by(Itinerary.conversation_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {int(conv_id): int(count) for conv_id, count in rows}


class ItineraryItemRepository(BaseRepository[ItineraryItem]):
    def __init__(self, session) -> None:
        super().__init__(session, ItineraryItem)

    async def list_by_itinerary(self, itinerary_id: int) -> list[ItineraryItem]:
        stmt = (
            select(ItineraryItem)
            .where(ItineraryItem.itinerary_id == itinerary_id)
            .order_by(ItineraryItem.seq.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def bulk_add(self, items: list[ItineraryItem]) -> None:
        self.session.add_all(items)
        await self.session.flush()


class ChangeEventRepository(BaseRepository[ChangeEvent]):
    def __init__(self, session) -> None:
        super().__init__(session, ChangeEvent)

    async def append(
        self,
        event_type: str,
        entity_type: str,
        entity_id: int,
        payload: dict,
        affected_itinerary_ids: list[int] | None = None,
    ) -> ChangeEvent:
        event = ChangeEvent(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            affected_itinerary_ids=affected_itinerary_ids or [],
            occurred_at=datetime.now(),
        )
        return await self.add(event)

    async def list_by_entity(
        self, entity_type: str, entity_id: int, since: datetime | None = None
    ) -> list[ChangeEvent]:
        stmt = (
            select(ChangeEvent)
            .where(ChangeEvent.entity_type == entity_type, ChangeEvent.entity_id == entity_id)
            .order_by(ChangeEvent.occurred_at.desc())
        )
        if since is not None:
            stmt = stmt.where(ChangeEvent.occurred_at >= since)
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_affecting(
        self, itinerary_id: int, since: datetime | None = None
    ) -> list[ChangeEvent]:
        stmt = (
            select(ChangeEvent)
            .where(ChangeEvent.affected_itinerary_ids.contains([itinerary_id]))
            .order_by(ChangeEvent.occurred_at.desc())
        )
        if since is not None:
            stmt = stmt.where(ChangeEvent.occurred_at >= since)
        return list((await self.session.execute(stmt)).scalars().all())