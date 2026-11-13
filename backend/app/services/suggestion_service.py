from __future__ import annotations

from datetime import date

from app.core.errors import BizError
from app.core.logging import get_logger
from app.repositories.itinerary_repo import ItineraryRepository
from app.services.replan_service import ReplanChanges, ReplanService

logger = get_logger("app.services.suggestion")

CONFIRMED_STATUS = "已确认"


def snapshot_visit_date(itinerary) -> date | None:
    snapshot = itinerary.request_snapshot or {}
    raw = snapshot.get("visit_date")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


class SuggestionService:
    def __init__(self, replan_service: ReplanService | None = None) -> None:
        self.replan_service = replan_service or ReplanService()

    async def _generate(self, session_factory, itineraries, changes: ReplanChanges) -> list[dict]:
        results: list[dict] = []
        for itinerary in itineraries:
            if itinerary.status != CONFIRMED_STATUS:
                continue
            try:
                outcome = await self.replan_service.refresh(
                    session_factory,
                    itinerary.conversation_id,
                    itinerary.id,
                    "data_change",
                    changes,
                )
            except BizError as exc:
                results.append(
                    {"plan_id": str(itinerary.id), "created": False, "reason": exc.message}
                )
                continue
            except Exception as exc:
                logger.warning(
                    "suggestion generation failed",
                    extra={"kind": "system", "plan_id": itinerary.id, "error": str(exc)[:200]},
                )
                results.append(
                    {"plan_id": str(itinerary.id), "created": False, "reason": "生成失败"}
                )
                continue
            results.append(
                {
                    "plan_id": str(itinerary.id),
                    "created": outcome.get("new_plan") is not None,
                    "conflict": outcome.get("conflict"),
                    "cost_delta": (outcome.get("diff") or {}).get("cost_delta"),
                }
            )
        return results

    async def on_price_change(
        self,
        session_factory,
        poi_id: int,
        new_price: float,
        affected_itinerary_ids: list[int],
    ) -> list[dict]:
        if not affected_itinerary_ids:
            return []
        async with session_factory() as session:
            repo = ItineraryRepository(session)
            itineraries = await repo.list_affected(list(affected_itinerary_ids))
        changes = ReplanChanges(price_hike={"poi_id": int(poi_id), "new_price": float(new_price)})
        return await self._generate(session_factory, itineraries, changes)

    async def on_poi_closed(
        self,
        session_factory,
        poi_id: int,
        affected_itinerary_ids: list[int],
    ) -> list[dict]:
        if not affected_itinerary_ids:
            return []
        async with session_factory() as session:
            repo = ItineraryRepository(session)
            itineraries = await repo.list_affected(list(affected_itinerary_ids))
        changes = ReplanChanges(poi_closed={"poi_id": int(poi_id)})
        return await self._generate(session_factory, itineraries, changes)

    async def on_weather_alert(
        self,
        session_factory,
        visit_date: date,
        alert_level: str,
    ) -> list[dict]:
        async with session_factory() as session:
            repo = ItineraryRepository(session)
            confirmed = await repo.list_confirmed()
        hit = [i for i in confirmed if snapshot_visit_date(i) == visit_date]
        if not hit:
            return []
        changes = ReplanChanges(heavy_rain=True)
        return await self._generate(session_factory, hit, changes)