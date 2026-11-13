from __future__ import annotations

from app.optimizer.models import (
    DEFAULT_TUNING,
    Goal,
    ItineraryItem,
    ItineraryPlan,
    OptimizeProblem,
    TuningParams,
)
from app.optimizer.solver import evaluate_sequence


def validate_itinerary(
    plan: ItineraryPlan,
    problem: OptimizeProblem,
    tuning: TuningParams = DEFAULT_TUNING,
) -> list[str]:
    violations: list[str] = []
    constraints = problem.constraints
    weekday = constraints.weekday

    if plan.total_cost > constraints.budget + 1e-6:
        violations.append("budget_exceeded")

    for item in plan.items:
        if item.kind != "sight":
            continue
        poi = problem.poi(item.poi_id)
        if poi is None or not poi.status_active:
            violations.append("inactive_poi_used")
            continue
        window = poi.window_on(weekday)
        if window is None:
            violations.append("poi_closed_on_date")
            continue
        open_minute, close_minute = window
        if item.start_minute < open_minute or item.end_minute > close_minute:
            violations.append("open_window_violation")

    sights = [i for i in plan.items if i.kind == "sight"]
    ordered = sorted(plan.items, key=lambda i: i.start_minute)
    for first, second in zip(ordered, ordered[1:]):
        if second.start_minute < first.end_minute - 1e-9:
            violations.append("time_overlap")

    if plan.items:
        end = max(i.end_minute for i in plan.items)
        if end > constraints.end_minute_limit:
            violations.append("duration_exceeded")

    if sights:
        day_start = constraints.start_minute
        day_end = max(i.end_minute for i in plan.items)
        crosses_lunch = day_start < 810 and day_end > 690
        if crosses_lunch and not plan.has_lunch():
            violations.append("missing_lunch")

    return violations


def repair_plan(
    plan: ItineraryPlan,
    problem: OptimizeProblem,
    tuning: TuningParams = DEFAULT_TUNING,
) -> ItineraryPlan | None:
    constraints = problem.constraints
    sights = [i for i in plan.items if i.kind == "sight"]
    if not sights:
        return None

    end = max(i.end_minute for i in plan.items)
    overshoot = end - constraints.end_minute_limit
    if overshoot <= 0:
        return plan
    if overshoot > tuning.repair_slack_minutes:
        return None

    trimmed = sights[:-1]
    if not trimmed:
        return None
    sequence = tuple(i.poi_id for i in trimmed)
    repaired = evaluate_sequence(problem, sequence, plan.goal, tuning)
    if repaired is None:
        return None
    if validate_itinerary(repaired, problem, tuning):
        return None
    return repaired