from __future__ import annotations

from app.agent.nodes.common import build_problem
from app.agent.state import AgentResponse, TourState
from app.optimizer import optimize as engine_optimize
from app.optimizer.candidates import build_sequence_pool
from app.optimizer.models import DEFAULT_TUNING, InfeasibilityReport, PlanBundle
from app.optimizer.prefilter import prefilter

INFEASIBLE_RESPONSE = (
    "当前条件下暂时无法生成可行行程。您可以尝试：增加预算、延长游玩时间、"
    "更换游玩日期或放宽兴趣主题，我会立即为您重新规划。"
)


def make_generate_candidates_node():
    async def generate_candidates_node(state: TourState) -> dict:
        problem = build_problem(state)
        pf = prefilter(problem, DEFAULT_TUNING)
        if not pf.survivors:
            return _infeasible_update(state, problem)
        pool = build_sequence_pool(problem, pf.survivors, DEFAULT_TUNING)
        if not pool.sequences:
            return _infeasible_update(state, problem)
        return {"candidate_plans": list(pool.sequences)}

    generate_candidates_node.__name__ = "generate_candidates"
    return generate_candidates_node


def _infeasible_update(state: TourState, problem) -> dict:
    result = engine_optimize(problem)
    if isinstance(result, PlanBundle):
        plans = [p for p in (result.save_money, result.save_time, result.balanced) if p is not None]
        if plans:
            return {"candidate_plans": [tuple(p.poi_ids()) for p in plans]}
    report = result if isinstance(result, InfeasibilityReport) else InfeasibilityReport()
    update: dict = {"candidate_plans": [], "infeasibility": report}
    if state.get("final_response") is None:
        update["final_response"] = AgentResponse(response_type="fallback", content=INFEASIBLE_RESPONSE)
    return update