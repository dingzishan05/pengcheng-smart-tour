from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tools import optimize_itinerary, validate_itinerary_tool
from app.optimizer.models import InfeasibilityReport, PlanBundle
from tests.optimizer.fixtures import make_problem


async def test_optimize_tool_returns_plans():
    problem = make_problem(budget=200, hours=8)
    result = await optimize_itinerary(problem)
    assert result.ok
    assert result.data.infeasibility is None
    assert 1 <= len(result.data.plans) <= 3
    assert result.data.solver in ("enumerate", "cpat")
    assert result.data.elapsed_ms >= 0
    for plan in result.data.plans:
        assert plan.total_cost <= 200 + 1e-6


async def test_optimize_tool_infeasibility():
    from app.tools import Coordinate
    from app.optimizer.models import CandidatePoi

    expensive = [
        CandidatePoi(
            poi_id=10, name="高价景区", visit_minutes=120, ticket_price=500,
            interest_score=0.9, tags=["汉文化"], indoor=True,
            lat=34.27, lng=117.21, open_windows={d: (540, 1080) for d in range(7)},
        ),
    ]
    problem = make_problem(budget=80, hours=6, pois=expensive)
    result = await optimize_itinerary(problem)
    assert result.ok
    assert result.data.plans == []
    assert result.data.infeasibility is not None
    assert result.data.infeasibility.reasons


async def test_validate_tool_pass_and_fix():
    problem = make_problem(budget=200, hours=8)
    result = await optimize_itinerary(problem)
    assert result.ok
    plan = result.data.plans[0]
    validation = await validate_itinerary_tool(plan, problem)
    assert validation.ok
    assert validation.data.passed is True
    assert validation.data.violations == []


async def test_validate_tool_detects_budget_violation():
    problem = make_problem(budget=200, hours=8)
    result = await optimize_itinerary(problem)
    plan = result.data.plans[0]
    tampered = plan.model_copy(deep=True)
    tampered.total_cost = 99999
    validation = await validate_itinerary_tool(tampered, problem)
    assert validation.ok
    assert validation.data.passed is False
    assert any(v.kind == "budget_exceeded" for v in validation.data.violations)