from __future__ import annotations

from itertools import combinations

from app.optimizer.models import (
    DEFAULT_TUNING,
    Goal,
    ItineraryPlan,
    PlanBundle,
    TuningParams,
)
from app.optimizer.solver import GoalSolutions

SIMILAR_NOTE = "符合您要求的组合有限，三套方案较为相似，可调整兴趣或时长获取更多差异"


def _kendall_tau(seq_a: list[int], seq_b: list[int]) -> float:
    common = [p for p in seq_a if p in seq_b]
    if len(common) < 2:
        return 0.0
    concordant = 0
    discordant = 0
    for x, y in combinations(common, 2):
        pos_ax = seq_a.index(x)
        pos_ay = seq_a.index(y)
        pos_bx = seq_b.index(x)
        pos_by = seq_b.index(y)
        if (pos_ax - pos_ay) * (pos_bx - pos_by) > 0:
            concordant += 1
        else:
            discordant += 1
    total = concordant + discordant
    return (concordant - discordant) / total if total else 0.0


def plan_similarity(a: ItineraryPlan, b: ItineraryPlan, tuning: TuningParams = DEFAULT_TUNING) -> float:
    set_a = set(a.poi_ids())
    set_b = set(b.poi_ids())
    union = set_a | set_b
    if not union:
        return 1.0
    jaccard = len(set_a & set_b) / len(union)
    tau = _kendall_tau(a.poi_ids(), b.poi_ids())
    return tuning.jaccard_weight * jaccard + tuning.kendall_weight * max(tau, 0.0)


def _pick_divergent(
    pool: list[ItineraryPlan],
    settled: list[ItineraryPlan],
    tuning: TuningParams,
) -> ItineraryPlan | None:
    if not pool:
        return None
    return min(
        pool,
        key=lambda cand: max(
            (plan_similarity(cand, s, tuning) for s in settled),
            default=0.0,
        ),
    )


def export_plans(
    solutions: dict[Goal, GoalSolutions],
    tuning: TuningParams = DEFAULT_TUNING,
) -> PlanBundle:
    bundle = PlanBundle()
    chosen: dict[Goal, ItineraryPlan] = {}

    for goal in (Goal.SAVE_MONEY, Goal.SAVE_TIME, Goal.BALANCED):
        sol = solutions.get(goal)
        if sol is None or sol.best is None:
            continue
        best = sol.best.model_copy(deep=True)
        if goal == Goal.SAVE_MONEY:
            best = _break_tie_by_interest(sol.ranked, tuning).model_copy(deep=True)
        if sol.solver_note:
            best.solver_note = sol.solver_note
        chosen[goal] = best

    settled: list[ItineraryPlan] = []
    for goal in (Goal.SAVE_MONEY, Goal.SAVE_TIME, Goal.BALANCED):
        plan = chosen.get(goal)
        if plan is None:
            continue
        others = [p for p in settled]
        similar = any(plan_similarity(plan, other, tuning) > tuning.dedup_threshold for other in others)
        if similar:
            pool = [p for p in solutions[goal].ranked[1:] if p is not None]
            replacement = _pick_divergent(pool, others + [plan], tuning)
            if replacement is not None and replacement.poi_ids() != plan.poi_ids():
                replacement = replacement.model_copy(deep=True)
                replacement.goal = goal
                if solutions[goal].solver_note:
                    replacement.solver_note = solutions[goal].solver_note
                chosen[goal] = replacement
                plan = replacement
            else:
                plan.similar_note = SIMILAR_NOTE
        settled.append(plan)

    bundle.save_money = chosen.get(Goal.SAVE_MONEY)
    bundle.save_time = chosen.get(Goal.SAVE_TIME)
    bundle.balanced = chosen.get(Goal.BALANCED)
    return bundle


def _break_tie_by_interest(ranked: list[ItineraryPlan], tuning: TuningParams) -> ItineraryPlan:
    if not ranked:
        raise ValueError("empty ranked list")
    best_cost = min(p.total_cost for p in ranked)
    within = [p for p in ranked if p.total_cost <= best_cost * (1 + tuning.money_tolerance)]
    # 同价位下优先覆盖更多兴趣点，而不是只选一个点就结束。
    return max(
        within,
        key=lambda p: (p.interest_score * len(p.poi_ids()), -p.total_cost),
    )