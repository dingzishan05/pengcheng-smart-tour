from __future__ import annotations

from datetime import date, datetime, time

from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.repositories.itinerary_repo import ChangeEventRepository, ItineraryRepository
from app.repositories.knowledge_repo import AuditLogRepository
from app.repositories.poi_repo import PoiHoursRepository, PriceRepository

logger = get_logger("app.scheduler.expiry_patrol")


async def expiry_patrol(session_factory=SessionFactory) -> dict:
    today = date.today()
    day_start = datetime.combine(today, time.min)
    stats = {"stale_prices": 0, "stale_hours": 0, "events_created": 0, "skipped": 0}
    async with session_factory() as session:
        price_repo = PriceRepository(session)
        hours_repo = PoiHoursRepository(session)
        event_repo = ChangeEventRepository(session)
        itinerary_repo = ItineraryRepository(session)
        audit_repo = AuditLogRepository(session)

        expired_prices = await price_repo.list_expired_current(today)
        expired_hours = await hours_repo.list_expired(today)
        stats["stale_prices"] = len(expired_prices)
        stats["stale_hours"] = len(expired_hours)

        poi_ids = sorted(
            {r.poi_id for r in expired_prices} | {h.poi_id for h in expired_hours}
        )
        for poi_id in poi_ids:
            existing = await event_repo.list_by_entity("poi", poi_id, since=day_start)
            if any((e.payload or {}).get("kind") == "data_stale" for e in existing):
                stats["skipped"] += 1
                continue
            price_part = [r for r in expired_prices if r.poi_id == poi_id]
            hours_part = [h for h in expired_hours if h.poi_id == poi_id]
            affected = [i.id for i in await itinerary_repo.list_referencing(poi_id)]
            await event_repo.append(
                event_type="价格变更" if price_part else "POI状态变更",
                entity_type="poi",
                entity_id=poi_id,
                payload={
                    "kind": "data_stale",
                    "date": today.isoformat(),
                    "expired_prices": len(price_part),
                    "expired_hours": len(hours_part),
                },
                affected_itinerary_ids=affected,
            )
            await audit_repo.append(
                operator_id=0,
                action="expiry_patrol",
                entity_type="poi",
                entity_id=poi_id,
                after_value={"expired_prices": len(price_part), "expired_hours": len(hours_part)},
            )
            stats["events_created"] += 1
        await session.commit()
    logger.info(
        "expiry_patrol done",
        extra={"kind": "system", **stats},
    )
    return stats