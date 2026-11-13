from __future__ import annotations

import time
from dataclasses import replace

from app.agent.nodes.common import build_problem
from app.agent.state import AgentResponse, TourState
from app.core.config import settings
from app.core.logging import get_logger
from app.optimizer import optimize as engine_optimize
from app.optimizer.export import export_plans
from app.optimizer.models import DEFAULT_TUNING, InfeasibilityReport, PlanBundle
from app.optimizer.solver import solve

logger = get_logger("app.agent.optimize")

INFEASIBLE_RESPONSE = (
    "当前条件下暂时无法生成可行行程。您可以尝试：增加预算、延长游玩时间、"
    "更换游玩日期或放宽兴趣主题，我会立即为您重新规划。"
)


def _tuning_with_budget(tuning, deadline: float | None, now) -> tuple:
    """图级 30s 预算监视器：按剩余时间收紧求解时限，为 explain 预留降级时间。"""
    if deadline is None:
        return tuning, None
    remaining = deadline - now()
    reserve = float(settings.explain_fallback_reserve_seconds)
    allowance = remaining - reserve
    if allowance >= tuning.cpsat_time_limit:
        return tuning, remaining
    tightened = replace(tuning, cpsat_time_limit=max(1.0, min(tuning.cpsat_time_limit, allowance)))
    return tightened, remaining


def make_optimize_node(clock=None):
    now = clock or time.monotonic

    async def optimize_node(state: TourState) -> dict:
        problem = build_problem(state)
        sequences = list(state.get("candidate_plans") or [])
        tuning, remaining = _tuning_with_budget(
            DEFAULT_TUNING, state.get("budget_deadline"), now
        )
        if remaining is not None and remaining < float(settings.explain_fallback_reserve_seconds):

            logger.warning(
                "tour budget nearly exhausted, solver time limit tightened",
                extra={
                    "kind": "algorithm",
                    "remaining_seconds": round(remaining, 2),
                    "cpsat_time_limit": tuning.cpsat_time_limit,
                },
            )
        solutions = solve(problem, sequences, tuning)
        bundle = export_plans(solutions, DEFAULT_TUNING)
        plans = [p for p in (bundle.save_money, bundle.save_time, bundle.balanced) if p is not None]
        if not plans:
            result = engine_optimize(problem)
            if isinstance(result, PlanBundle):
                plans = [p for p in (result.save_money, result.save_time, result.balanced) if p is not None]
                if plans:
                    return {"plans": plans}
            report = result if isinstance(result, InfeasibilityReport) else InfeasibilityReport()
            return {
                "plans": None,
                "infeasibility": report,
                "final_response": AgentResponse(response_type="fallback", content=INFEASIBLE_RESPONSE),
            }
        return {"plans": plans}

    optimize_node.__name__ = "optimize"
    return optimize_node