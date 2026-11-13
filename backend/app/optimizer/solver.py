from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.optimizer.models import (
    DEFAULT_TUNING,
    Goal,
    ItineraryItem,
    ItineraryPlan,
    OptimizeProblem,
    TransportMode,
    TransportSegment,
    TuningParams,
    mode_fare,
)

try:
    from ortools.sat.python import cp_model as _cp_model
except ImportError:
    _cp_model = None


@dataclass
class GoalSolutions:
    ranked: list[ItineraryPlan] = field(default_factory=list)
    solver_note: str | None = None

    @property
    def best(self) -> ItineraryPlan | None:
        return self.ranked[0] if self.ranked else None


def _choose_mode(
    goal: Goal,
    modes: list[TransportMode],
    edge_fares: dict[TransportMode, float],
    edge_minutes: dict[TransportMode, int],
) -> TransportMode:
    if not modes:
        raise ValueError("no transport mode available")
    if goal == Goal.SAVE_TIME:
        return min(modes, key=lambda m: edge_minutes[m])
    if goal == Goal.SAVE_MONEY:
        cheapest = min(edge_fares[m] for m in modes)
        candidates = [m for m in modes if edge_fares[m] == cheapest]
        return min(candidates, key=lambda m: edge_minutes[m])
    if TransportMode.TRANSIT in modes:
        return TransportMode.TRANSIT
    return min(modes, key=lambda m: edge_minutes[m])


def evaluate_sequence(
    problem: OptimizeProblem,
    sequence: tuple[int, ...],
    goal: Goal,
    tuning: TuningParams = DEFAULT_TUNING,
) -> ItineraryPlan | None:
    constraints = problem.constraints
    weekday = constraints.weekday
    limit = constraints.end_minute_limit
    tier = tuning.meal_tier(constraints.budget)
    meal_cost = tier * constraints.party_size

    ticket_cost = 0.0
    transit_cost = 0.0
    transit_minutes = 0
    interest_scores: list[float] = []

    items: list[ItineraryItem] = []
    segments: list[TransportSegment] = []

    current_minute = constraints.start_minute
    prev_id = -1
    lunch_inserted = False
    budget_left = constraints.budget

    for poi_id in sequence:
        poi = problem.poi(poi_id)
        if poi is None:
            return None
        edge = problem.edge(prev_id, poi_id)
        if edge is None or not edge.available_modes():
            return None
        modes = edge.available_modes()
        fares = {m: mode_fare(m, edge, constraints.party_size, tuning) for m in modes}
        minutes = {m: tuning.buffered_minutes(edge.option(m).minutes) for m in modes}

        window = poi.window_on(weekday)
        if window is None:
            return None
        open_minute, close_minute = window

        mode = _choose_mode(goal, modes, fares, minutes)
        travel = minutes[mode]
        fare = fares[mode]
        arrive = current_minute + travel
        visit_start = max(arrive, open_minute)
        visit_end = visit_start + poi.visit_minutes

        if not lunch_inserted and visit_end > 690:
            lunch = ItineraryItem(
                poi_id=poi_id,
                name="午餐",
                kind="meal",
                start_minute=current_minute,
                end_minute=current_minute + tuning.meal_minutes,
                ticket_price=tier,
            )
            items.append(lunch)
            lunch_inserted = True
            if transit_cost + ticket_cost + meal_cost > constraints.budget:
                return None
            current_minute = lunch.end_minute
            arrive = current_minute + travel
            visit_start = max(arrive, open_minute)
            visit_end = visit_start + poi.visit_minutes

        if visit_end > close_minute or visit_end > limit:
            return None

        ticket = poi.ticket_price * constraints.party_size
        if ticket_cost + transit_cost + fare + ticket + (meal_cost if lunch_inserted else 0) > constraints.budget:
            return None

        items.append(
            ItineraryItem(
                poi_id=poi_id,
                name=poi.name,
                kind="sight",
                start_minute=visit_start,
                end_minute=visit_end,
                ticket_price=poi.ticket_price,
                is_reference_price=poi.is_reference_price,
                interest_score=poi.interest_score,
                tags=poi.tags,
            )
        )
        segments.append(
            TransportSegment(
                from_poi_id=prev_id,
                to_poi_id=poi_id,
                mode=mode,
                minutes=travel,
                fare=fare,
                distance_meters=edge.option(mode).distance_meters,
            )
        )
        ticket_cost += ticket
        transit_cost += fare
        transit_minutes += travel
        interest_scores.append(poi.interest_score)
        current_minute = visit_end
        prev_id = poi_id

    if prev_id == -1:
        return None

    total_cost = round(ticket_cost + transit_cost + meal_cost if lunch_inserted else ticket_cost + transit_cost, 2)
    plan = ItineraryPlan(
        goal=goal,
        items=items,
        segments=segments,
        total_cost=total_cost,
        ticket_cost=round(ticket_cost, 2),
        transit_cost=round(transit_cost, 2),
        meal_cost=meal_cost if lunch_inserted else 0,
        transit_minutes=transit_minutes,
        total_minutes=current_minute - constraints.start_minute,
        interest_score=round(sum(interest_scores) / len(interest_scores), 4) if interest_scores else 0,
    )
    return plan


def _sight_count(plan: ItineraryPlan) -> int:
    return max(1, len(plan.poi_ids()))


def _objective_value(plan: ItineraryPlan) -> float:
    if plan.goal == Goal.SAVE_MONEY:
        return plan.total_cost
    if plan.goal == Goal.SAVE_TIME:
        return plan.transit_minutes
    interest_sum = plan.interest_score * _sight_count(plan)
    return plan.total_cost + 0.5 * plan.transit_minutes - 10.0 * interest_sum


def solve(
    problem: OptimizeProblem,
    sequences: list[tuple[int, ...]],
    tuning: TuningParams = DEFAULT_TUNING,
) -> dict[Goal, GoalSolutions]:
    results: dict[Goal, GoalSolutions] = {}
    pool = sequences
    note = None

    if len(pool) > tuning.enum_threshold:
        if _cp_model is not None:
            pool = sequences[: tuning.enum_threshold]
            note = "cpsat_fallback_truncated"
        else:
            pool = sequences[: tuning.enum_threshold]
            note = "enumeration_truncated"

    evaluated_by_goal: dict[Goal, list[ItineraryPlan]] = {}
    max_sights = 0
    for goal in Goal:
        evaluated: list[ItineraryPlan] = []
        for seq in pool:
            plan = evaluate_sequence(problem, seq, goal, tuning)
            if plan is not None:
                evaluated.append(plan)
                max_sights = max(max_sights, len(plan.poi_ids()))
        evaluated_by_goal[goal] = evaluated

    # 三套方案必须“日程量级”可比，否则“省时”会退化成只排一个点（少去点当然更省时），
    # “省钱”也只排一个免费点，最终三张卡片雷同且根本不像一日游。
    coverage_floor = max(1, math.ceil(max_sights * 0.6)) if max_sights else 1

    for goal, evaluated in evaluated_by_goal.items():
        covered = [p for p in evaluated if len(p.poi_ids()) >= coverage_floor]
        if covered:
            evaluated = covered
        if goal == Goal.SAVE_MONEY:
            tolerance = tuning.money_tolerance
            best_cost = min((p.total_cost for p in evaluated), default=None)
            if best_cost is not None:
                within = [p for p in evaluated if p.total_cost <= best_cost * (1 + tolerance)]
                evaluated.sort(key=lambda p: (-p.interest_score, p.total_cost))
                evaluated = within + [p for p in evaluated if p not in within]
            else:
                evaluated.sort(key=_objective_value)
        else:
            evaluated.sort(key=_objective_value)
        results[goal] = GoalSolutions(
            ranked=evaluated[: tuning.secondary_pool_size],
            solver_note=note,
        )
    return results
