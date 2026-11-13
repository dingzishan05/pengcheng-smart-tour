from __future__ import annotations

import random
from datetime import date

from app.optimizer import (
    CandidatePoi,
    InfeasibilityReport,
    PlanBundle,
    WeatherInfo,
    optimize,
    validate_itinerary,
)
from app.optimizer.export import plan_similarity
from app.optimizer.models import TuningParams, haversine_km
from tests.optimizer.fixtures import ALL_WEEK, NO_MONDAY, ORIGIN, make_pois, make_problem


def _bundle_or_fail(problem):
    result = optimize(problem)
    assert isinstance(result, PlanBundle), getattr(result, "reasons", None)
    return result


def test_budget_150_all_plans_within_budget():
    bundle = _bundle_or_fail(make_problem(budget=150, hours=8))
    for plan in (bundle.save_money, bundle.save_time, bundle.balanced):
        assert plan is not None
        assert plan.total_cost <= 150 + 1e-6


def test_monday_closed_pois_excluded():
    problem = make_problem(tour_date=date(2026, 10, 19))
    assert problem.constraints.tour_date.weekday() == 0
    bundle = _bundle_or_fail(problem)
    for plan in (bundle.save_money, bundle.save_time, bundle.balanced):
        assert 1 not in plan.poi_ids()
        assert 5 not in plan.poi_ids()


def test_duration_4h_respected():
    bundle = _bundle_or_fail(make_problem(budget=150, hours=4))
    for plan in (bundle.save_money, bundle.save_time, bundle.balanced):
        assert plan.total_minutes <= 4 * 60 + 1e-9


def test_save_money_cheapest():
    bundle = _bundle_or_fail(make_problem(budget=300, hours=8))
    assert bundle.save_money.total_cost <= bundle.save_time.total_cost + 1e-6
    assert bundle.save_money.total_cost <= bundle.balanced.total_cost + 1e-6


def test_save_time_least_transit():
    bundle = _bundle_or_fail(make_problem(budget=300, hours=8))
    assert bundle.save_time.transit_minutes <= bundle.save_money.transit_minutes
    assert bundle.save_time.transit_minutes <= bundle.balanced.transit_minutes


def test_full_day_plan_contains_lunch():
    bundle = _bundle_or_fail(make_problem(budget=200, hours=8))
    for plan in (bundle.save_money, bundle.save_time, bundle.balanced):
        if plan.total_minutes > 4 * 60:
            assert plan.has_lunch(), "跨午餐时段的方案必须包含午餐项"
            assert plan.meal_cost > 0


def test_interest_tag_preference():
    bundle = _bundle_or_fail(
        make_problem(budget=150, hours=8, interest_tags=["汉文化"])
    )
    for plan in (bundle.save_money, bundle.save_time, bundle.balanced):
        sights = plan.poi_ids()
        tagged = [p for p in sights if p in (1, 3, 5, 6)]
        assert len(tagged) >= max(1, len(sights) * 2 // 3)


def test_transport_segments_exported():
    bundle = _bundle_or_fail(make_problem(budget=150, hours=8))
    plan = bundle.balanced
    assert plan.segments
    for seg in plan.segments:
        assert seg.mode in ("步行", "公交", "驾车")
        assert seg.minutes > 0
        assert seg.fare >= 0
    sight_ids = [-1] + plan.poi_ids()
    assert [s.to_poi_id for s in plan.segments] == sight_ids[1:]


def test_short_trip_radius_filter():
    bundle = _bundle_or_fail(make_problem(budget=150, hours=4))
    for plan in (bundle.save_money, bundle.save_time, bundle.balanced):
        assert 8 not in plan.poi_ids()


def test_pinned_poi_included():
    bundle = _bundle_or_fail(make_problem(budget=200, hours=6, pinned=[3]))
    for plan in (bundle.save_money, bundle.save_time, bundle.balanced):
        assert 3 in plan.poi_ids()


def test_similar_note_on_converged_plans():
    bundle = _bundle_or_fail(
        make_problem(budget=150, hours=4, interest_tags=["汉文化"])
    )
    plans = [bundle.save_money, bundle.save_time, bundle.balanced]
    if all(plan_similarity(plans[0], p) > 0.8 for p in plans[1:]):
        assert any(p.similar_note for p in plans)


def test_no_feasible_plan_diagnosis_budget():
    expensive = [
        CandidatePoi(
            poi_id=10, name="豪华景区", visit_minutes=120, ticket_price=90,
            interest_score=0.9, tags=["汉文化"], indoor=True,
            lat=34.27, lng=117.21, open_windows=dict(ALL_WEEK),
        ),
        CandidatePoi(
            poi_id=11, name="温泉度假村", visit_minutes=120, ticket_price=85,
            interest_score=0.7, tags=["度假"], indoor=True,
            lat=34.275, lng=117.215, open_windows=dict(ALL_WEEK),
        ),
        CandidatePoi(
            poi_id=12, name="郊野公园", visit_minutes=120, ticket_price=0,
            interest_score=0.6, tags=["公园"], indoor=False,
            lat=34.05, lng=117.55, open_windows=dict(ALL_WEEK),
        ),
    ]
    problem = make_problem(budget=80, hours=6, interest_tags=["汉文化"], pois=expensive)
    result = optimize(problem)
    assert isinstance(result, InfeasibilityReport)
    assert result.reasons
    assert any(r.kind == "budget" for r in result.reasons)
    assert result.advice.min_budget_suggestion is not None
    assert "预算" in result.advice.min_budget_suggestion
    assert result.advice.free_first is True


def test_weather_rain_filters_outdoor():
    problem = make_problem(budget=150, hours=8)
    problem.weather = WeatherInfo(condition_text="暴雨", has_heavy_rain=True)
    bundle = _bundle_or_fail(problem)
    indoor_ids = {p.poi_id for p in problem.candidates if p.indoor}
    for plan in (bundle.save_money, bundle.save_time, bundle.balanced):
        assert set(plan.poi_ids()) <= indoor_ids


def test_validate_itinerary_detects_violations():
    problem = make_problem(budget=100, hours=8)
    bundle = _bundle_or_fail(problem)
    plan = bundle.save_money
    assert validate_itinerary(plan, problem) == []
    tampered = plan.model_copy(deep=True)
    tampered.total_cost = problem.constraints.budget + 50
    assert "budget_exceeded" in validate_itinerary(tampered, problem)

CITY_HOP_KM = 15.0
REMOTE_IDS = {8}


def _adjacent_distances(problem, poi_ids):
    coords = {p.poi_id: (p.lat, p.lng) for p in problem.candidates}
    coords[-1] = ORIGIN
    seq = [-1, *poi_ids]
    return [
        (seq[i], seq[i + 1], haversine_km(*coords[seq[i]], *coords[seq[i + 1]]))
        for i in range(len(seq) - 1)
    ]


def test_visit_order_no_east_west_zigzag():
    problem = make_problem(budget=300, hours=8)
    bundle = _bundle_or_fail(problem)
    for plan in (bundle.save_money, bundle.save_time, bundle.balanced):
        for a, b, dist in _adjacent_distances(problem, plan.poi_ids()):
            if a in REMOTE_IDS or b in REMOTE_IDS:
                continue
            assert dist < CITY_HOP_KM, f"顺序存在跨城横跳：{a}->{b} 距离 {dist:.1f}km"


def test_short_trip_order_all_within_city():
    problem = make_problem(budget=200, hours=4)
    bundle = _bundle_or_fail(problem)
    for plan in (bundle.save_money, bundle.save_time, bundle.balanced):
        assert 8 not in plan.poi_ids()
        for a, b, dist in _adjacent_distances(problem, plan.poi_ids()):
            assert dist < CITY_HOP_KM, f"顺序存在跨城横跳：{a}->{b} 距离 {dist:.1f}km"


def test_pool_truncation_sets_solver_note():
    problem = make_problem(budget=300, hours=8)
    tuning = TuningParams(enum_threshold=3)
    result = optimize(problem, tuning=tuning)
    assert isinstance(result, PlanBundle)
    for plan in (result.save_money, result.save_time, result.balanced):
        assert plan is not None
        assert plan.solver_note and "truncated" in plan.solver_note


def test_random_100_problems_statistical_run():
    rng = random.Random(2026)
    tags_pool = ["汉文化", "博物馆", "自然风光", "美食", "公园", "历史文化", "游乐园"]
    budgets = [80, 120, 150, 200, 250, 300, 400]
    hours_pool = [3, 4, 5, 6, 7, 8, 9]
    feasible = 0
    for _ in range(100):
        problem = make_problem(
            budget=rng.choice(budgets),
            hours=rng.choice(hours_pool),
            tour_date=date(2026, 10, rng.randint(19, 25)),
            interest_tags=rng.sample(tags_pool, rng.randint(0, 3)),
        )
        if rng.random() < 0.15:
            problem.weather = WeatherInfo(condition_text="暴雨", has_heavy_rain=True)
        result = optimize(problem)
        if isinstance(result, InfeasibilityReport):
            assert result.reasons
            continue
        feasible += 1
        assert isinstance(result, PlanBundle)
        for plan in (result.save_money, result.save_time, result.balanced):
            if plan is None:
                continue
            assert plan.total_cost <= problem.constraints.budget + 1e-6
            assert plan.total_minutes <= problem.constraints.hours * 60 + 1e-9
            assert validate_itinerary(plan, problem) == []
    assert feasible >= 50, f"随机需求可行率过低：{feasible}/100"