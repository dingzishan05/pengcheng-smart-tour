from __future__ import annotations

from typing import Callable

from app.optimizer.models import (
    DEFAULT_TUNING,
    InfeasibilityAdvice,
    InfeasibleReason,
    InfeasibleReasonKind,
    InfeasibilityReport,
    OptimizeProblem,
    TuningParams,
)
from app.optimizer.prefilter import PrefilterResult, Removal

FeasibleFn = Callable[[float], bool]


def _reasons_from_removals(removed: list[Removal]) -> list[InfeasibleReason]:
    reasons: list[InfeasibleReason] = []
    closed = [r for r in removed if r.reason == "closed_on_date"]
    unreachable = [r for r in removed if r.reason in ("unreachable_from_origin",)]
    over_budget = [r for r in removed if r.reason == "ticket_over_budget"]
    inactive = [r for r in removed if r.reason == "status_inactive"]

    if closed:
        reasons.append(
            InfeasibleReason(
                kind=InfeasibleReasonKind.CLOSED,
                detail=f"{len(closed)} 个候选景点在所选日期闭馆（如需游览请更换日期）",
            )
        )
    if inactive:
        reasons.append(
            InfeasibleReason(
                kind=InfeasibleReasonKind.CLOSED,
                detail=f"{len(inactive)} 个候选景点已失效停用",
            )
        )
    if unreachable:
        reasons.append(
            InfeasibleReason(
                kind=InfeasibleReasonKind.UNREACHABLE,
                detail="部分景点从出发地无可用交通方案",
            )
        )
    if over_budget:
        reasons.append(
            InfeasibleReason(
                kind=InfeasibleReasonKind.BUDGET,
                detail=f"{len(over_budget)} 个候选景点单人门票已超出当前预算",
            )
        )
    return reasons


def _binary_search_min_budget(
    feasible_fn: FeasibleFn,
    base_budget: float,
    tuning: TuningParams,
) -> float | None:
    low, high = 0.0, base_budget * 3
    if not feasible_fn(high):
        return None
    if feasible_fn(low):
        return low
    for _ in range(12):
        mid = (low + high) / 2
        if feasible_fn(mid):
            high = mid
        else:
            low = mid
        if high - low < 1.0:
            break
    return round(high, 0)


def diagnose(
    problem: OptimizeProblem,
    prefilter_result: PrefilterResult | None = None,
    feasible_fn: FeasibleFn | None = None,
    no_feasible_sequence: bool = False,
    tuning: TuningParams = DEFAULT_TUNING,
) -> InfeasibilityReport:
    constraints = problem.constraints
    reasons: list[InfeasibleReason] = []

    if prefilter_result is not None:
        reasons.extend(_reasons_from_removals(prefilter_result.removed))

    survivors = prefilter_result.survivors if prefilter_result is not None else problem.candidates
    if survivors:
        if no_feasible_sequence:
            min_ticket = min(p.ticket_price for p in survivors) * constraints.party_size
            if min_ticket * 3 > constraints.budget:
                reasons.append(
                    InfeasibleReason(
                        kind=InfeasibleReasonKind.BUDGET,
                        detail=f"当前预算 {constraints.budget:.0f} 元无法覆盖候选景点的门票与交通组合",
                    )
                )
            reasons.append(
                InfeasibleReason(
                    kind=InfeasibleReasonKind.DURATION,
                    detail=f"{constraints.hours:.1f} 小时内无法完成任一候选组合（含交通与游览时长）",
                )
            )
    else:
        if not reasons:
            reasons.append(
                InfeasibleReason(
                    kind=InfeasibleReasonKind.CLOSED,
                    detail="没有满足条件的候选景点",
                )
            )

    advice = InfeasibilityAdvice()

    if feasible_fn is not None:
        min_budget = _binary_search_min_budget(feasible_fn, constraints.budget, tuning)
        if min_budget is not None and min_budget > constraints.budget:
            advice.min_budget_suggestion = f"建议将预算增至约 {min_budget:.0f} 元"

    free_exists = any(
        p.is_free()
        and p.status_active
        and p.window_on(constraints.weekday) is not None
        and problem.edge(-1, p.poi_id) is not None
        for p in problem.candidates
    )
    if free_exists:
        advice.free_first = True

    if any(r.kind == InfeasibleReasonKind.DURATION for r in reasons):
        advice.duration_suggestion = (
            f"可延长可用时长（当前 {constraints.hours:.1f} 小时）或减少期望景点数量"
        )

    return InfeasibilityReport(reasons=reasons, advice=advice)
