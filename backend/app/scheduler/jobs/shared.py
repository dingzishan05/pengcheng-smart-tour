from __future__ import annotations

from datetime import date, datetime, time

from app.core.logging import get_logger
from app.repositories.itinerary_repo import ChangeEventRepository, ItineraryRepository
from app.services.suggestion_service import SuggestionService, snapshot_visit_date

logger = get_logger("app.scheduler.shared")


async def issue_weather_alert(session_factory, visit_date: date, level: str) -> dict:
    day_start = datetime.combine(visit_date, time.min)
    async with session_factory() as session:
        event_repo = ChangeEventRepository(session)
        entity_id = int(visit_date.toordinal())
        existing = await event_repo.list_by_entity("weather", entity_id)
        already = any(
            (e.payload or {}).get("kind") == "weather_alert"
            and (e.payload or {}).get("date") == visit_date.isoformat()
            and (e.payload or {}).get("alert_level") == level
            for e in existing
        )
        if already:
            return {"event": False, "reason": "duplicate"}

        itinerary_repo = ItineraryRepository(session)
        confirmed = await itinerary_repo.list_confirmed()
        affected = [
            i.id
            for i in confirmed
            if snapshot_visit_date(i) == visit_date
        ]
        await event_repo.append(
            event_type="天气异常",
            entity_type="weather",
            entity_id=entity_id,
            payload={
                "kind": "weather_alert",
                "date": visit_date.isoformat(),
                "alert_level": level,
            },
            affected_itinerary_ids=affected,
        )
        await session.commit()

    suggestions = await SuggestionService().on_weather_alert(
        session_factory, visit_date, level
    )
    stats = {"event": True, "affected_itinerary_ids": affected, "suggestions": suggestions}
    logger.info(
        "weather alert issued",
        extra={"kind": "system", "date": visit_date.isoformat(), "level": level, "affected": len(affected)},
    )
    return stats