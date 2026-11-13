from __future__ import annotations

from dataclasses import dataclass

from app.optimizer.models import (
    CandidatePoi,
    OptimizeProblem,
    TuningParams,
    haversine_km,
)


@dataclass
class SequencePool:
    sequences: list[tuple[int, ...]]
    topk: list[CandidatePoi]
    truncated: bool = False


def _topk_order(problem: OptimizeProblem, survivors: list[CandidatePoi], tuning: TuningParams) -> list[CandidatePoi]:
    constraints = problem.constraints
    distances = {
        poi.poi_id: haversine_km(constraints.origin_lat, constraints.origin_lng, poi.lat, poi.lng)
        for poi in survivors
    }
    max_distance = max(distances.values(), default=1.0) or 1.0
    pinned = set(constraints.pinned_pois)

    def score(poi: CandidatePoi) -> float:
        proximity = 1.0 - distances[poi.poi_id] / max_distance
        value = 0.6 * poi.interest_score + 0.4 * proximity
        if poi.poi_id in pinned:
            value += 10.0
        return value

    ordered = sorted(survivors, key=score, reverse=True)
    keep = ordered[: tuning.top_k]
    for poi in ordered:
        if poi.poi_id in pinned and poi not in keep:
            keep.append(poi)
    return keep


def _earliest_travel_minutes(problem: OptimizeProblem, from_id: int, to_id: int) -> int | None:
    edge = problem.edge(from_id, to_id)
    if edge is None:
        return None
    options = [o.minutes for o in (edge.walk, edge.transit, edge.drive) if o is not None]
    return min(options) if options else None


def build_sequence_pool(problem: OptimizeProblem, survivors: list[CandidatePoi], tuning: TuningParams) -> SequencePool:
    constraints = problem.constraints
    total_minutes = int(constraints.hours * 60)
    pinned = set(constraints.pinned_pois)
    topk = _topk_order(problem, survivors, tuning)

    feasible_single = []
    for poi in topk:
        outbound = _earliest_travel_minutes(problem, -1, poi.poi_id)
        inbound = _earliest_travel_minutes(problem, poi.poi_id, -1)
        if outbound is None or inbound is None:
            continue
        if outbound + poi.visit_minutes + inbound <= total_minutes:
            feasible_single.append(poi)

    pool: list[tuple[int, ...]] = []
    truncated = False
    max_visits = problem.max_visits()

    def dfs(current: list[CandidatePoi], used_minutes: int, budget_left: float) -> None:
        nonlocal truncated
        if len(pool) >= tuning.pool_limit:
            truncated = True
            return
        if current:
            seq = tuple(p.poi_id for p in current)
            if pinned <= set(seq):
                pool.append(seq)
        if len(current) >= max_visits or len(current) >= len(feasible_single):
            return
        for poi in feasible_single:
            if any(p.poi_id == poi.poi_id for p in current):
                continue
            prev_id = current[-1].poi_id if current else -1
            travel = _earliest_travel_minutes(problem, prev_id, poi.poi_id)
            if travel is None:
                continue
            optimistic = used_minutes + travel + poi.visit_minutes
            optimistic += _earliest_travel_minutes(problem, poi.poi_id, -1) or 0
            if optimistic > total_minutes:
                continue
            ticket_left = budget_left - poi.ticket_price * constraints.party_size
            if ticket_left < 0:
                continue
            current.append(poi)
            dfs(current, used_minutes + travel + poi.visit_minutes, ticket_left)
            current.pop()
            if len(pool) >= tuning.pool_limit:
                truncated = True
                return

    dfs([], 0, constraints.budget)

    ordered = list(dict.fromkeys(pool))
    if not ordered:
        for poi in feasible_single:
            ordered.append((poi.poi_id,))
    return SequencePool(sequences=ordered[: tuning.pool_limit], topk=feasible_single, truncated=truncated or len(ordered) > tuning.pool_limit)