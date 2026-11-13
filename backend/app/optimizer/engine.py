from __future__ import annotations

from app.optimizer.candidates import build_sequence_pool
from app.optimizer.diagnose import diagnose
from app.optimizer.export import export_plans
from app.optimizer.models import (
    DEFAULT_TUNING,
    InfeasibilityReport,
    OptimizeProblem,
    PlanBundle,
    TuningParams,
)
from app.optimizer.prefilter import prefilter
from app.optimizer.solver import solve
from app.optimizer.validate import repair_plan, validate_itinerary


def _make_feasible_fn(problem: OptimizeProblem, tuning: TuningParams):
    def feasible(budget: float) -> bool:
        adjusted = problem.model_copy(deep=True)
        adjusted.constraints.budget = max(budget, 0)
        pf = prefilter(adjusted, tuning)
        if not pf.survivors:
            return False
        pool = build_sequence_pool(adjusted, pf.survivors, tuning)
        if not pool.sequences:
            return False
        solutions = solve(adjusted, pool.sequences, tuning)
        return any(s.best is not None for s in solutions.values())

    return feasible


def optimize(
    problem: OptimizeProblem,
    tuning: TuningParams | None = None,
) -> PlanBundle | InfeasibilityReport:
    tp = tuning or DEFAULT_TUNING

    pf = prefilter(problem, tp)
    if not pf.survivors:
        return diagnose(
            problem,
            prefilter_result=pf,
            feasible_fn=_make_feasible_fn(problem, tp) if problem.candidates else None,
            tuning=tp,
        )

    pool = build_sequence_pool(problem, pf.survivors, tp)
    if not pool.sequences:
        return diagnose(
            problem,
            prefilter_result=pf,
            feasible_fn=_make_feasible_fn(problem, tp),
            no_feasible_sequence=True,
            tuning=tp,
        )

    solutions = solve(problem, pool.sequences, tp)
    if all(s.best is None for s in solutions.values()):
        return diagnose(
            problem,
            prefilter_result=pf,
            feasible_fn=_make_feasible_fn(problem, tp),
            no_feasible_sequence=True,
            tuning=tp,
        )

    bundle = export_plans(solutions, tp)

    for goal_field in ("save_money", "save_time", "balanced"):
        plan = getattr(bundle, goal_field)
        if plan is None:
            continue
        if validate_itinerary(plan, problem, tp):
            repaired = repair_plan(plan, problem, tp)
            if repaired is not None and not validate_itinerary(repaired, problem, tp):
                setattr(bundle, goal_field, repaired)
            else:
                sol = solutions.get(plan.goal)
                fallback = None
                for candidate in (sol.ranked if sol else []):
                    if candidate.poi_ids() != plan.poi_ids() and not validate_itinerary(candidate, problem, tp):
                        continue
                    if not validate_itinerary(candidate, problem, tp):
                        fallback = candidate
                        break
                if fallback is not None:
                    setattr(bundle, goal_field, fallback)

    if bundle.save_money is None and bundle.save_time is None and bundle.balanced is None:
        return diagnose(
            problem,
            prefilter_result=pf,
            feasible_fn=_make_feasible_fn(problem, tp),
            no_feasible_sequence=True,
            tuning=tp,
        )

    return bundle