from __future__ import annotations

from app.agent.nodes.common import build_problem
from app.agent.state import TourState
from app.optimizer.models import DEFAULT_TUNING, ItineraryPlan
from app.optimizer.validate import repair_plan, validate_itinerary

VIOLATION_NOTE = "存在轻微校验偏差，仅供参考"


def make_validate_node():
    async def validate_node(state: TourState) -> dict:
        problem = build_problem(state)
        plans: list[ItineraryPlan] = list(state.get("plans") or [])
        validated: list[ItineraryPlan] = []
        reference_items: list[int] = []
        for plan in plans:
            reference_items.extend(
                item.poi_id for item in plan.items if item.kind == "sight" and item.is_reference_price
            )
            if not validate_itinerary(plan, problem, DEFAULT_TUNING):
                repaired = repair_plan(plan, problem, DEFAULT_TUNING)
                if repaired is not None and not validate_itinerary(repaired, problem, DEFAULT_TUNING):
                    validated.append(repaired)
                else:
                    plan.solver_note = f"{plan.solver_note}；{VIOLATION_NOTE}" if plan.solver_note else VIOLATION_NOTE
                    validated.append(plan)
            else:
                validated.append(plan)
        return {
            "plans": validated,
            "reference_items": sorted(set(reference_items)),
        }

    validate_node.__name__ = "validate"
    return validate_node