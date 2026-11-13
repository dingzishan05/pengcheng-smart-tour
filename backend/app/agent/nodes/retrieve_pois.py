from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.agent.nodes.common import (
    apply_defaults,
    name_match,
    normalize_interests,
    resolve_visit_date,
)
from app.agent.state import (
    AgentError,
    AgentResponse,
    StructuredRequest,
    TourState,
)
from app.core.logging import get_logger
from app.optimizer.models import InfeasibilityAdvice, InfeasibilityReport, InfeasibleReason, InfeasibleReasonKind
from app.tools.pois import Coordinate, PoiCandidate, PoiQuery, search_attractions

logger = get_logger("app.agent.retrieve_pois")

SHORT_TRIP_RADIUS_KM = 10.0

EMPTY_CANDIDATES_RESPONSE = (
    "很抱歉，游玩当日暂无符合条件的景点可安排（可能当日闭馆或兴趣范围内无开放景点）。"
    "建议您更换游玩日期或调整兴趣主题，例如“想去自然风光类景点”。"
)


def _query_from(request: StructuredRequest, visit: date) -> PoiQuery:
    center = Coordinate(lat=request.origin.lat, lng=request.origin.lng) if request.origin else None
    return PoiQuery(
        tags=normalize_interests(request.interests),
        day_of_week=visit.weekday(),
        budget_cap=Decimal(str(request.budget)) if request.budget is not None else Decimal("0"),
        center=center,
        radius_km=SHORT_TRIP_RADIUS_KM if request.short_trip else None,
        short_trip=request.short_trip,
    )


def make_retrieve_pois_node(session_factory):
    async def retrieve_pois_node(state: TourState) -> dict:
        request: StructuredRequest = state["structured_request"]
        visit = resolve_visit_date(request, state.get("visit_date"))
        merged, notes = apply_defaults(request, visit)
        query = _query_from(merged, visit)

        try:
            async with session_factory() as session:
                result = await search_attractions(session, query)
                candidates: list[PoiCandidate] = list(result.data or []) if result.ok else []
                if result.ok and merged.pinned_pois:
                    found_names = {c.name for c in candidates}
                    missing_pins = [
                        p for p in merged.pinned_pois
                        if not any(name_match(n, p) for n in found_names)
                    ]
                    if missing_pins:
                        loose = PoiQuery(
                            tags=[],
                            day_of_week=visit.weekday(),
                            budget_cap=Decimal(str(merged.budget)) if merged.budget is not None else Decimal("0"),
                            center=None,
                            radius_km=None,
                        )
                        loose_result = await search_attractions(session, loose)
                        if loose_result.ok:
                            for cand in loose_result.data or []:
                                if any(name_match(cand.name, p) for p in missing_pins):
                                    candidates.append(cand)
        except Exception as exc:

            logger.error("retrieve_pois failed", extra={"kind": "external", "error": str(exc)[:200]})
            return {
                "error": AgentError(code="50005", message="POI 检索失败"),
                "final_response": AgentResponse(response_type="fallback", content="数据服务暂时不可用，请稍后重试。"),
            }

        if not candidates:
            report = InfeasibilityReport(
                reasons=[
                    InfeasibleReason(
                        kind=InfeasibleReasonKind.CLOSED,
                        detail="游玩当日开放且符合兴趣/预算条件的候选景点为空",
                    )
                ],
                advice=InfeasibilityAdvice(free_first=True),
            )
            return {
                "structured_request": merged,
                "defaults_applied": notes,
                "visit_date": merged.visit_date.isoformat() if merged.visit_date else visit.isoformat(),
                "candidate_pois": [],
                "infeasibility": report,
                "final_response": AgentResponse(response_type="fallback", content=EMPTY_CANDIDATES_RESPONSE),
            }

        return {
            "structured_request": merged,
            "defaults_applied": notes,
            "visit_date": merged.visit_date.isoformat() if merged.visit_date else visit.isoformat(),
            "candidate_pois": candidates,
        }

    retrieve_pois_node.__name__ = "retrieve_pois"
    return retrieve_pois_node