from __future__ import annotations

from app.optimizer import (
    PlanBundle,
    optimize,
    replan_budget_change,
    replan_data_change,
    replan_replace_item,
)
from tests.optimizer.fixtures import make_problem


def _plan():
    problem = make_problem(budget=300, hours=8, pinned=[3, 5, 6])
    result = optimize(problem)
    assert isinstance(result, PlanBundle)
    return problem, result.balanced


def _plan_with_outdoor():
    problem = make_problem(budget=200, hours=6, pinned=[3, 2])
    result = optimize(problem)
    assert isinstance(result, PlanBundle)
    return problem, result.balanced


def test_budget_change_300_to_150_diff():
    problem, plan = _plan()
    assert plan.total_cost > 150
    result = replan_budget_change(plan, 150.0, problem)
    assert result.new_plan.total_cost <= 150 + 1e-6
    assert result.pending is True
    assert result.diff.cost_delta < 0
    old_ids, new_ids = set(plan.poi_ids()), set(result.new_plan.poi_ids())
    assert old_ids != new_ids
    removed = old_ids - new_ids
    assert removed, "预算 300→150 应移除高费项"
    assert result.diff.removed_poi_ids == sorted(removed)


def test_budget_change_keeps_unaffected_items():
    problem, plan = _plan()
    result = replan_budget_change(plan, 180.0, problem)
    old_by_id = {i.poi_id: i for i in plan.items if i.kind == "sight"}
    new_by_id = {i.poi_id: i for i in result.new_plan.items if i.kind == "sight"}
    for poi_id, old_item in old_by_id.items():
        if poi_id in new_by_id:
            assert new_by_id[poi_id].start_minute == old_item.start_minute
            assert new_by_id[poi_id].end_minute == old_item.end_minute


def test_price_hike_within_budget_only_updates_price():
    problem, plan = _plan()
    target_id = plan.poi_ids()[0]
    old_price = next(i.ticket_price for i in plan.items if i.poi_id == target_id)
    result = replan_data_change(plan, "price_hike", target_id, old_price + 10, problem)
    assert result is not None
    old_by_id = {i.poi_id: i for i in plan.items if i.kind == "sight"}
    new_by_id = {i.poi_id: i for i in result.new_plan.items if i.kind == "sight"}
    for poi_id, old_item in old_by_id.items():
        if poi_id in new_by_id and poi_id != target_id:
            assert new_by_id[poi_id].start_minute == old_item.start_minute
    assert result.diff.removed_poi_ids == []
    assert result.diff.added_poi_ids == []
    assert result.diff.cost_delta >= 0


def test_price_hike_over_budget_triggers_replan():
    problem, plan = _plan()
    target_id = next(
        i.poi_id for i in plan.items if i.kind == "sight" and i.ticket_price > 0
    )
    result = replan_data_change(plan, "price_hike", target_id, 500.0, problem)
    assert result is not None
    assert result.pending is True
    assert target_id not in result.new_plan.poi_ids()


def test_closed_poi_replaced():
    problem, plan = _plan()
    target_id = plan.poi_ids()[0]
    target_poi = problem.poi(target_id)
    result = replan_replace_item(plan, target_id, problem)
    assert result is not None
    assert target_id not in result.new_plan.poi_ids()
    assert result.diff.added_poi_ids
    replacement = problem.poi(result.diff.added_poi_ids[0])
    assert replacement is not None
    assert set(replacement.tags) & set(target_poi.tags) or replacement.interest_score >= 0.6
    assert result.reason


def test_heavy_rain_replaces_outdoor_with_indoor():
    problem, plan = _plan_with_outdoor()
    assert 2 in plan.poi_ids()
    outdoor_ids = {
        i.poi_id for i in plan.items
        if i.kind == "sight" and not problem.poi(i.poi_id).indoor
    }
    if not outdoor_ids:
        outdoor_ids = {i.poi_id for i in plan.items if i.kind == "sight"}
    result = replan_data_change(plan, "heavy_rain", None, None, problem)
    if outdoor_ids:
        assert result is not None
        assert result.pending is True
        indoor_ids = {p.poi_id for p in problem.candidates if p.indoor}
        assert set(result.new_plan.poi_ids()) <= indoor_ids | (set(plan.poi_ids()) - outdoor_ids)


def test_replan_budget_change_detail_message():
    problem, plan = _plan()
    result = replan_budget_change(plan, 150.0, problem)
    assert "移除" in result.diff.detail
    assert result.diff.cost_delta == round(result.new_plan.total_cost - plan.total_cost, 2)