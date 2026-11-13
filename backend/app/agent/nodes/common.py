from __future__ import annotations

import re
from datetime import date

from app.agent.state import (
    OriginInfo,
    StructuredRequest,
    TourState,
    route_matrix_to_optimizer,
)
from app.optimizer.models import (
    CandidatePoi,
    Constraints,
    OptimizeProblem,
)
from app.tools.pois import PoiCandidate

DEFAULT_DURATION_HOURS = 4.0
DEFAULT_BUDGET = 500.0
DEFAULT_INTERESTS: list[str] = ["历史", "汉文化", "休闲"]
DEFAULT_ORIGIN = OriginInfo(name="彭城广场（市中心）", lat=34.2570, lng=117.1840)

LANDMARK_COORDS: dict[str, tuple[float, float]] = {
    "徐州站": (34.2643, 117.2010),
    "徐州东站": (34.2589, 117.3139),
    "观音机场": (34.0614, 117.5622),
    "彭城广场": (34.2570, 117.1840),
    "淮海广场": (34.2630, 117.1990),
    "云龙湖": (34.2350, 117.1600),
    "户部山": (34.2490, 117.1880),
}

INTEREST_TAG_MAP: dict[str, list[str]] = {
    "历史": ["历史", "汉文化"],
    "历史文化": ["历史", "汉文化"],
    "文化": ["历史", "汉文化"],
    "博物馆": ["历史", "汉文化"],
    "文物": ["历史", "汉文化"],
    "人文": ["历史", "民俗"],
    "汉": ["汉文化", "历史"],
    "汉文化": ["汉文化", "历史"],
    "汉代": ["汉文化", "历史"],
    "兵马俑": ["汉文化", "历史"],
    "汉墓": ["汉文化", "历史"],
    "汉朝": ["汉文化", "历史"],
    "美食": ["美食"],
    "吃": ["美食"],
    "小吃": ["美食"],
    "特色菜": ["美食"],
    "自然": ["自然风光", "休闲"],
    "自然风光": ["自然风光", "休闲"],
    "风景": ["自然风光", "休闲"],
    "湖": ["自然风光", "休闲"],
    "山": ["自然风光", "休闲"],
    "公园": ["自然风光", "休闲"],
    "亲子": ["亲子", "休闲"],
    "孩子": ["亲子", "休闲"],
    "小孩": ["亲子", "休闲"],
    "家庭": ["亲子", "休闲"],
    "购物": ["购物"],
    "逛街": ["购物"],
    "商场": ["购物"],
    "红色": ["红色文化", "历史"],
    "抗战": ["红色文化", "历史"],
    "革命": ["红色文化", "历史"],
    "民俗": ["民俗", "历史"],
    "古民居": ["民俗", "历史"],
    "传统": ["民俗", "历史"],
    "休闲": ["休闲"],
    "放松": ["休闲"],
    "散步": ["休闲"],
}


def normalize_interests(terms: list[str]) -> list[str]:
    result: list[str] = []
    for term in terms:
        mapped = INTEREST_TAG_MAP.get(term.strip())
        if mapped:
            result.extend(mapped)
        else:
            result.append(term.strip())
    return list(dict.fromkeys(t for t in result if t))


def geocode_origin(text: str | None) -> OriginInfo | None:
    if not text:
        return None
    for name, (lat, lng) in LANDMARK_COORDS.items():
        if name in text:
            return OriginInfo(name=name, lat=lat, lng=lng)
    return None


def apply_defaults(request: StructuredRequest, visit_date: date) -> tuple[StructuredRequest, list[str]]:
    merged = request.model_copy(deep=True)
    notes: list[str] = []
    if merged.duration_hours is None:
        merged.duration_hours = DEFAULT_DURATION_HOURS
        notes.append("游玩时长未提供，已按半天（约4小时）估算")
    if merged.budget is None:
        merged.budget = DEFAULT_BUDGET
        notes.append("预算未提供，已按默认预算500元估算")
    if not merged.interests:
        merged.interests = list(DEFAULT_INTERESTS)
        notes.append("兴趣未指定，已按均衡偏好（历史、汉文化、休闲）推荐")
    if merged.origin is None:
        merged.origin = DEFAULT_ORIGIN.model_copy(deep=True)
        notes.append("出发地未提供，已按市中心（彭城广场）估算")
    if merged.visit_date is None:
        merged.visit_date = visit_date
        notes.append("游玩日期未指定，已按今日安排")
    merged.short_trip = merged.duration_hours is not None and merged.duration_hours <= 6
    return merged, notes


def parse_party_size(companions: str | None) -> int:
    if not companions:
        return 1
    if any(w in companions for w in ("一家", "家人", "孩子", "小孩", "亲子", "老人")):
        return 3
    if any(w in companions for w in ("两个人", "两人", "情侣", "夫妻", "朋友")):
        return 2
    digits = re.findall(r"[1-9]", companions)
    return int(digits[0]) if digits else 1


def name_match(poi_name: str, pinned: str) -> bool:
    return pinned in poi_name or poi_name in pinned


def to_optimizer_candidate(poi: PoiCandidate, weekday: int) -> CandidatePoi:
    windows: dict[int, tuple[int, int]] = {}
    if poi.open_window is not None:
        windows[weekday] = (poi.open_window.open_minute, poi.open_window.close_minute)
    return CandidatePoi(
        poi_id=poi.poi_id,
        name=poi.name,
        visit_minutes=max(1, poi.visit_minutes),
        ticket_price=poi.ticket_price,
        is_reference_price=False,
        interest_score=poi.interest_score,
        tags=list(poi.tags),
        lat=poi.location.lat,
        lng=poi.location.lng,
        indoor=poi.indoor,
        status_active=True,
        open_windows=windows,
    )


def resolve_visit_date(request: StructuredRequest, state_visit_date: str | None) -> date:
    if request.visit_date is not None:
        return request.visit_date
    if state_visit_date:
        try:
            return date.fromisoformat(state_visit_date)
        except ValueError:
            pass
    return date.today()


def build_problem(state: TourState) -> OptimizeProblem:
    request: StructuredRequest = state["structured_request"]
    visit = resolve_visit_date(request, state.get("visit_date"))
    weekday = visit.weekday()
    candidates = [
        to_optimizer_candidate(p, weekday) for p in (state.get("candidate_pois") or [])
    ]
    pinned_ids = [
        c.poi_id
        for c in candidates
        if any(name_match(c.name, n) for n in request.pinned_pois)
    ]
    origin = request.origin or DEFAULT_ORIGIN
    constraints = Constraints(
        tour_date=visit,
        budget=float(request.budget or 0),
        hours=float(request.duration_hours or DEFAULT_DURATION_HOURS),
        start_minute=9 * 60,
        origin_name=origin.name,
        origin_lat=origin.lat,
        origin_lng=origin.lng,
        party_size=parse_party_size(request.companions),
        pinned_pois=pinned_ids,
        interest_tags=normalize_interests(request.interests),
    )
    return OptimizeProblem(
        candidates=candidates,
        route_matrix=route_matrix_to_optimizer(state.get("route_matrix") or {}),
        constraints=constraints,
        weather=state.get("weather"),
    )