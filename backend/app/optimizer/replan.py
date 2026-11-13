from __future__ import annotations

from app.optimizer.models import (
    DEFAULT_TUNING,
    Goal,
    ItineraryItem,
    ItineraryPlan,
    OptimizeProblem,
    PlanDiff,
    ReplanResult,
    TransportMode,
    TransportSegment,
    TuningParams,
    haversine_km,
    mode_fare,
)


def _sights(plan: ItineraryPlan) -> list[ItineraryItem]:
    return [i for i in plan.items if i.kind == "sight"]


def _rebuild_after_removal(
    plan: ItineraryPlan,
    removed_ids: set[int],
    problem: OptimizeProblem,
    tuning: TuningParams,
) -> ItineraryPlan:
    constraints = problem.constraints
    items = [i for i in plan.items if i.poi_id not in removed_ids or i.kind == "meal"]
    sights = [i for i in items if i.kind == "sight"]

    new_segments: list[TransportSegment] = []
    prev_id = -1
    transit_cost = 0.0
    transit_minutes = 0
    for item in sights:
        edge = problem.edge(prev_id, item.poi_id)
        if edge is None:
            edge = problem.edge(item.poi_id, prev_id)
        if edge is None or not edge.available_modes():
            continue
        modes = edge.available_modes()
        mode = min(
            modes,
            key=lambda m: tuning.buffered_minutes(edge.option(m).minutes),
        )
        travel = tuning.buffered_minutes(edge.option(mode).minutes)
        fare = mode_fare(mode, edge, constraints.party_size, tuning)
        new_segments.append(
            TransportSegment(
                from_poi_id=prev_id,
                to_poi_id=item.poi_id,
                mode=mode,
                minutes=travel,
                fare=fare,
                distance_meters=edge.option(mode).distance_meters,
            )
        )
        transit_cost += fare
        transit_minutes += travel
        prev_id = item.poi_id

    meal_items = [i for i in items if i.kind == "meal"]
    ticket_cost = sum(i.ticket_price for i in sights) * constraints.party_size
    meal_cost = sum(i.ticket_price for i in meal_items) * constraints.party_size
    total = round(ticket_cost + transit_cost + meal_cost, 2)

    interest_scores = [i.interest_score for i in sights]
    return ItineraryPlan(
        goal=plan.goal,
        items=items,
        segments=new_segments,
        total_cost=total,
        ticket_cost=round(ticket_cost, 2),
        transit_cost=round(transit_cost, 2),
        meal_cost=round(meal_cost, 2),
        transit_minutes=transit_minutes,
        total_minutes=(max((i.end_minute for i in items), default=constraints.start_minute) - constraints.start_minute),
        interest_score=round(sum(interest_scores) / len(interest_scores), 4) if interest_scores else 0,
        similar_note=plan.similar_note,
        solver_note=plan.solver_note,
    )


def _neighbor_fill(
    plan: ItineraryPlan,
    removed_items: list[ItineraryItem],
    problem: OptimizeProblem,
    tuning: TuningParams,
) -> list[ItineraryItem]:
    constraints = problem.constraints
    used = {i.poi_id for i in _sights(plan)}
    added: list[ItineraryItem] = []
    budget_left = constraints.budget - plan.total_cost

    for removed in removed_items:
        gap_start = removed.start_minute
        gap_end = removed.end_minute
        gap = gap_end - gap_start
        best = None
        best_score = -1.0
        for candidate in problem.candidates:
            if candidate.poi_id in used or not candidate.status_active:
                continue
            if candidate.window_on(constraints.weekday) is None:
                continue
            if candidate.ticket_price * constraints.party_size > budget_left:
                continue
            if candidate.visit_minutes + 20 > gap:
                continue
            distance = haversine_km(constraints.origin_lat, constraints.origin_lng, candidate.lat, candidate.lng)
            if distance > 15:
                continue
            score = candidate.interest_score * 0.7 + (1 - distance / 15) * 0.3
            if score > best_score:
                best_score = score
                best = candidate
        if best is not None:
            start = gap_start
            added.append(
                ItineraryItem(
                    poi_id=best.poi_id,
                    name=best.name,
                    kind="sight",
                    start_minute=start,
                    end_minute=start + best.visit_minutes,
                    ticket_price=best.ticket_price,
                    is_reference_price=best.is_reference_price,
                    interest_score=best.interest_score,
                    tags=best.tags,
                )
            )
            used.add(best.poi_id)
            budget_left -= best.ticket_price * constraints.party_size

    return added


def replan_budget_change(
    original: ItineraryPlan,
    new_budget: float,
    problem: OptimizeProblem,
    tuning: TuningParams = DEFAULT_TUNING,
) -> ReplanResult:
    constraints = problem.constraints


    plan = original
    removed_ids: set[int] = set()
    removed_items: list[ItineraryItem] = []

    while plan.total_cost > new_budget + 1e-6:
        sights = _sights(plan)
        if not sights:
            break
        victim = sorted(
            sights,
            key=lambda i: (i.interest_score, -i.ticket_price),
        )[0]
        removed_ids.add(victim.poi_id)
        removed_items.append(victim)
        plan = _rebuild_after_removal(original, removed_ids, problem, tuning)

    added = _neighbor_fill(plan, removed_items, problem, tuning) if removed_items else []
    if added:
        items = sorted(plan.items + added, key=lambda i: i.start_minute)
        plan = plan.model_copy(deep=True)
        plan.items = items
        plan.total_cost = round(plan.total_cost + sum(i.ticket_price for i in added) * constraints.party_size, 2)
        plan.ticket_cost = round(plan.ticket_cost + sum(i.ticket_price for i in added) * constraints.party_size, 2)

    old_ids = set(original.poi_ids())
    new_ids = set(plan.poi_ids())
    diff = PlanDiff(
        removed_poi_ids=sorted(old_ids - new_ids),
        added_poi_ids=sorted(new_ids - old_ids),
        cost_delta=round(plan.total_cost - original.total_cost, 2),
        detail=(
            f"预算由 {original.total_cost:.0f} 元调整为 {plan.total_cost:.0f} 元；"
            f"移除 {len(removed_ids)} 项，补充 {len(added)} 项，未受影响项目时刻保持不变"
        ),
    )
    return ReplanResult(
        new_plan=plan,
        diff=diff,
        reason="预算变更触发保守重规划",
        pending=True,
    )


def replan_replace_item(
    original: ItineraryPlan,
    target_poi_id: int,
    problem: OptimizeProblem,
    tuning: TuningParams = DEFAULT_TUNING,
    exclude_poi_ids: list[int] | None = None,
) -> ReplanResult | None:
    constraints = problem.constraints
    target = next((i for i in original.items if i.poi_id == target_poi_id and i.kind == "sight"), None)
    target_poi = problem.poi(target_poi_id)
    if target is None or target_poi is None:
        return None

    used = {i.poi_id for i in _sights(original)}
    exclude = set(exclude_poi_ids or []) | used
    prev_ids = [s.from_poi_id for s in original.segments if s.to_poi_id == target_poi_id]
    prev_id = prev_ids[0] if prev_ids else -1

    best = None
    best_score = -1.0
    for candidate in problem.candidates:
        if candidate.poi_id in exclude or not candidate.status_active:
            continue
        window = candidate.window_on(constraints.weekday)
        if window is None:
            continue
        if candidate.visit_minutes > target.end_minute - target.start_minute + 30:
            continue
        edge = problem.edge(prev_id, candidate.poi_id)
        if edge is None or not edge.available_modes():
            continue
        tag_overlap = len(set(candidate.tags) & set(target_poi.tags))
        distance = haversine_km(target_poi.lat, target_poi.lng, candidate.lat, candidate.lng)
        score = candidate.interest_score + tag_overlap * 0.3 + max(0.0, 1 - distance / 10) * 0.3
        if score > best_score:
            best_score = score
            best = candidate

    if best is None:
        removed = {target_poi_id}
        plan = _rebuild_after_removal(original, removed, problem, tuning)
        diff = PlanDiff(
            removed_poi_ids=[target_poi_id],
            added_poi_ids=[],
            cost_delta=round(plan.total_cost - original.total_cost, 2),
            detail=f"未找到可替换 {target_poi.name} 的合适景点，建议直接移除该项",
        )
        return ReplanResult(new_plan=plan, diff=diff, reason="项目替换无候选，回退为移除", pending=True)

    shift = max(0, best.visit_minutes - (target.end_minute - target.start_minute))
    items: list[ItineraryItem] = []
    for item in original.items:
        if item.poi_id == target_poi_id and item.kind == "sight":
            items.append(
                ItineraryItem(
                    poi_id=best.poi_id,
                    name=best.name,
                    kind="sight",
                    start_minute=item.start_minute,
                    end_minute=item.start_minute + best.visit_minutes,
                    ticket_price=best.ticket_price,
                    is_reference_price=best.is_reference_price,
                    interest_score=best.interest_score,
                    tags=best.tags,
                )
            )
        elif item.start_minute >= target.end_minute:
            new_item = item.model_copy(deep=True)
            new_item.start_minute += shift
            new_item.end_minute += shift
            items.append(new_item)
        else:
            items.append(item)

    new_ids = set(original.poi_ids()) - {target_poi_id} | {best.poi_id}
    cost_delta = (best.ticket_price - target_poi.ticket_price) * constraints.party_size
    plan = original.model_copy(deep=True)
    plan.items = sorted(items, key=lambda i: i.start_minute)
    plan.total_cost = round(original.total_cost + cost_delta, 2)
    plan.ticket_cost = round(original.ticket_cost + cost_delta, 2)
    if new_ids and plan.interest_score:
        sight_items = [i for i in plan.items if i.kind == "sight"]
        plan.interest_score = round(sum(i.interest_score for i in sight_items) / len(sight_items), 4)

    tag_overlap = set(best.tags) & set(target_poi.tags)
    reason = (
        f"{best.name} 与 {target_poi.name}"
        + (f" 同为「{'、'.join(list(tag_overlap)[:2])}」主题" if tag_overlap else " 主题相近")
        + f"，游览时长 {best.visit_minutes} 分钟，可按原时段承接"
    )
    diff = PlanDiff(
        removed_poi_ids=[target_poi_id],
        added_poi_ids=[best.poi_id],
        cost_delta=round(cost_delta, 2),
        detail=reason,
    )
    return ReplanResult(new_plan=plan, diff=diff, reason=reason, pending=True)


def replan_data_change(
    original: ItineraryPlan,
    change_type: str,
    poi_id: int | None,
    new_price: float | None,
    problem: OptimizeProblem,
    tuning: TuningParams = DEFAULT_TUNING,
) -> ReplanResult | None:
    if change_type == "price_hike" and poi_id is not None and new_price is not None:
        item = next((i for i in original.items if i.poi_id == poi_id and i.kind == "sight"), None)
        if item is None:
            return None
        delta = (new_price - item.ticket_price) * problem.constraints.party_size
        hiked = original.model_copy(deep=True)
        for i in hiked.items:
            if i.poi_id == poi_id and i.kind == "sight":
                i.ticket_price = new_price
        hiked.total_cost = round(original.total_cost + delta, 2)
        hiked.ticket_cost = round(original.ticket_cost + delta, 2)
        if hiked.total_cost <= problem.constraints.budget:
            return ReplanResult(
                new_plan=hiked,
                diff=PlanDiff(removed_poi_ids=[], added_poi_ids=[], cost_delta=round(delta, 2), detail="涨价后仍在预算内，仅更新价格"),
                reason="价格更新（预算内，无需调整行程）",
                pending=True,
            )
        result = replan_budget_change(hiked, problem.constraints.budget, problem, tuning)
        result.reason = f"门票涨价超预算，按预算约束重新规划（涉及 {poi_id}）"
        return result

    if change_type == "poi_closed" and poi_id is not None:
        return replan_replace_item(original, poi_id, problem, tuning)

    if change_type == "heavy_rain":
        outdoor = [i for i in _sights(original) if not _is_indoor(problem, i.poi_id)]
        if not outdoor:
            return None
        # 暴雨时要把行程里所有户外项目都换掉，而不是只处理第一个。
        exclude = [c.poi_id for c in problem.candidates if not c.indoor]
        plan = original
        added: list[int] = []
        removed: list[int] = []
        for item in outdoor:
            if item.poi_id not in plan.poi_ids():
                continue
            result = replan_replace_item(
                plan, item.poi_id, problem, tuning, exclude_poi_ids=exclude
            )
            if result is None:
                continue
            plan = result.new_plan
            added.extend(result.diff.added_poi_ids)
            removed.extend(result.diff.removed_poi_ids)
        return ReplanResult(
            new_plan=plan,
            diff=PlanDiff(
                removed_poi_ids=sorted(set(removed)),
                added_poi_ids=sorted(set(added)),
                cost_delta=round(plan.total_cost - original.total_cost, 2),
                detail="暴雨预警：已将户外项目替换为室内景点",
            ),
            reason="暴雨预警，户外项目已替换为室内景点",
            pending=True,
        )

    return None


def _is_indoor(problem: OptimizeProblem, poi_id: int) -> bool:
    poi = problem.poi(poi_id)
    return poi.indoor if poi else True