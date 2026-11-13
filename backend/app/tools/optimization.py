from __future__ import annotations

import time
from pydantic import BaseModel, Field

from app.optimizer import optimize as engine_optimize
from app.optimizer import validate_itinerary as engine_validate
from app.optimizer.models import (
    InfeasibilityReport,
    ItineraryPlan,
    OptimizeProblem,
    PlanBundle,
)
from app.optimizer.validate import repair_plan
from app.tools.base import ToolErrorKind, ToolMeta, ToolResult

try:
    from ortools.sat.python import cp_model as _cp_model
    _SOLVER_NAME = "cpat"
except ImportError:
    _cp_model = None
    _SOLVER_NAME = "enumerate"


class OptimizeOutput(BaseModel):
    plans: list[ItineraryPlan] = Field(default_factory=list)
    solver: str = "enumerate"
    elapsed_ms: int = 0
    infeasibility: InfeasibilityReport | None = None


class Violation(BaseModel):
    kind: str
    detail: str = ""


class FixAction(BaseModel):
    action: str
    description: str = ""


class ValidationReport(BaseModel):
    passed: bool
    violations: list[Violation] = Field(default_factory=list)
    reference_items: list[int] = Field(default_factory=list)
    fix_applied: FixAction | None = None


async def optimize_itinerary(problem: OptimizeProblem) -> ToolResult[OptimizeOutput]:
    start = time.perf_counter()
    try:
        result = engine_optimize(problem)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        if isinstance(result, InfeasibilityReport):
            output = OptimizeOutput(
                plans=[],
                solver=_SOLVER_NAME,
                elapsed_ms=elapsed_ms,
                infeasibility=result,
            )
            return ToolResult.success(output, ToolMeta(source_channel="optimizer"))
        plans = [p for p in (result.save_money, result.save_time, result.balanced) if p is not None]
        output = OptimizeOutput(
            plans=plans,
            solver=_SOLVER_NAME,
            elapsed_ms=elapsed_ms,
            infeasibility=None,
        )
        return ToolResult.success(output, ToolMeta(source_channel="optimizer"))
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return ToolResult.failure(
            ToolErrorKind.ALGORITHM,
            "50006",
            f"行程优化失败: {exc.__class__.__name__}",
        )


async def validate_itinerary_tool(
    plan: ItineraryPlan, problem: OptimizeProblem
) -> ToolResult[ValidationReport]:
    try:
        violations = engine_validate(plan, problem)
        reference_items = [
            item.poi_id for item in plan.items if item.kind == "sight" and item.is_reference_price
        ]
        fix_applied: FixAction | None = None
        if violations:
            repaired = repair_plan(plan, problem)
            if repaired is not None:
                remaining = engine_validate(repaired, problem)
                if not remaining:
                    fix_applied = FixAction(
                        action="repair",
                        description="轻微超时已通过移除末位项修复",
                    )
                    plan = repaired
                    violations = []
        passed = not violations
        report = ValidationReport(
            passed=passed,
            violations=[Violation(kind=v) for v in violations],
            reference_items=reference_items,
            fix_applied=fix_applied,
        )
        return ToolResult.success(report, ToolMeta(source_channel="optimizer"))
    except Exception as exc:
        return ToolResult.failure(
            ToolErrorKind.ALGORITHM,
            "50006",
            f"行程校验失败: {exc.__class__.__name__}",
        )