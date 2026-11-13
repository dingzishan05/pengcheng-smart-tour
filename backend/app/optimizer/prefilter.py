from __future__ import annotations

from dataclasses import dataclass, field

from app.optimizer.models import (
    CandidatePoi,
    OptimizeProblem,
    TuningParams,
    haversine_km,
)


@dataclass
class Removal:
    poi_id: int
    reason: str


@dataclass
class PrefilterResult:
    survivors: list[CandidatePoi] = field(default_factory=list)
    removed: list[Removal] = field(default_factory=list)
    removed_pinned: list[int] = field(default_factory=list)


def prefilter(problem: OptimizeProblem, tuning: TuningParams) -> PrefilterResult:
    result = PrefilterResult()
    constraints = problem.constraints
    pinned = set(constraints.pinned_pois)

    active = []
    for poi in problem.candidates:
        if poi.status_active:
            active.append(poi)
        else:
            result.removed.append(Removal(poi.poi_id, "status_inactive"))
            if poi.poi_id in pinned:
                result.removed_pinned.append(poi.poi_id)

    open_today = []
    for poi in active:
        if poi.window_on(constraints.weekday) is not None:
            open_today.append(poi)
        else:
            result.removed.append(Removal(poi.poi_id, "closed_on_date"))
            if poi.poi_id in pinned:
                result.removed_pinned.append(poi.poi_id)

    weather_ok = []
    risky = problem.weather is not None and problem.weather.outdoor_risky
    for poi in open_today:
        if risky and not poi.indoor:
            result.removed.append(Removal(poi.poi_id, "weather_outdoor_risky"))
            continue
        weather_ok.append(poi)

    within_budget = []
    for poi in weather_ok:
        if poi.ticket_price * constraints.party_size <= constraints.budget:
            within_budget.append(poi)
        else:
            result.removed.append(Removal(poi.poi_id, "ticket_over_budget"))

    if constraints.hours <= tuning.short_hours:
        nearby = []
        for poi in within_budget:
            distance = haversine_km(
                constraints.origin_lat,
                constraints.origin_lng,
                poi.lat,
                poi.lng,
            )
            if distance <= tuning.short_radius_km:
                nearby.append(poi)
            else:
                result.removed.append(Removal(poi.poi_id, "beyond_short_radius"))
        within_budget = nearby

    tagged = {t for t in constraints.interest_tags if t}
    if tagged:
        matched = [
            poi for poi in within_budget if tagged & set(poi.tags) or poi.poi_id in pinned
        ]
        if matched:
            # 兴趣是软约束：匹配项太少时（如周一大量博物馆闭馆）补入其余开放景点，
            # 否则 8 小时只排 1—2 个点，三套方案必然雷同。
            if len(matched) < tuning.min_tag_pool:
                fillers = sorted(
                    (poi for poi in within_budget if poi not in matched),
                    key=lambda p: (-p.interest_score, p.poi_id),
                )
                matched = matched + fillers[: max(0, tuning.top_k - len(matched))]
            within_budget = matched

    reachable = []
    for poi in within_budget:
        edge = problem.edge(-1, poi.poi_id)
        if edge is not None and edge.available_modes():
            reachable.append(poi)
        else:
            result.removed.append(Removal(poi.poi_id, "unreachable_from_origin"))

    result.survivors = reachable
    return result