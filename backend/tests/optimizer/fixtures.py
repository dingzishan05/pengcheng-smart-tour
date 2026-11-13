from __future__ import annotations

import math
from datetime import date

from app.optimizer.models import (
    CandidatePoi,
    Constraints,
    OptimizeProblem,
    RouteEdge,
    RouteOption,
    haversine_km,
)

ORIGIN = (34.26, 117.20)

ALL_WEEK = {d: (540, 1080) for d in range(7)}
NO_MONDAY = {d: (540, 1080) for d in range(7) if d != 0}


def make_pois() -> list[CandidatePoi]:
    return [
        CandidatePoi(
            poi_id=1, name="徐州博物馆", visit_minutes=90, ticket_price=0,
            interest_score=0.9, tags=["博物馆", "汉文化", "历史文化"], indoor=True,
            lat=34.265, lng=117.195, open_windows=dict(NO_MONDAY),
        ),
        CandidatePoi(
            poi_id=2, name="户部山古建筑群", visit_minutes=60, ticket_price=0,
            interest_score=0.75, tags=["古建筑", "历史文化"], indoor=False,
            lat=34.25, lng=117.20, open_windows=dict(ALL_WEEK),
        ),
        CandidatePoi(
            poi_id=3, name="龟山汉墓", visit_minutes=120, ticket_price=60,
            interest_score=0.85, tags=["汉文化", "历史遗址"], indoor=True,
            lat=34.29, lng=117.17, open_windows=dict(ALL_WEEK),
        ),
        CandidatePoi(
            poi_id=4, name="云龙湖景区", visit_minutes=120, ticket_price=0,
            interest_score=0.7, tags=["自然风光", "公园"], indoor=False,
            lat=34.23, lng=117.16, open_windows=dict(ALL_WEEK),
        ),
        CandidatePoi(
            poi_id=5, name="汉兵马俑博物馆", visit_minutes=90, ticket_price=30,
            interest_score=0.8, tags=["汉文化", "博物馆"], indoor=True,
            lat=34.22, lng=117.28, open_windows=dict(NO_MONDAY),
        ),
        CandidatePoi(
            poi_id=6, name="汉画像石艺术馆", visit_minutes=80, ticket_price=40,
            interest_score=0.78, tags=["博物馆", "汉文化"], indoor=True,
            lat=34.24, lng=117.19, open_windows=dict(ALL_WEEK),
        ),
        CandidatePoi(
            poi_id=7, name="户部山明清街", visit_minutes=45, ticket_price=0,
            interest_score=0.6, tags=["美食", "街区"], indoor=False,
            lat=34.251, lng=117.201, open_windows=dict(ALL_WEEK),
        ),
        CandidatePoi(
            poi_id=8, name="远郊乐园", visit_minutes=120, ticket_price=0,
            interest_score=0.65, tags=["游乐园"], indoor=False,
            lat=34.10, lng=117.50, open_windows=dict(ALL_WEEK),
        ),
    ]


def _edge(lat1: float, lng1: float, lat2: float, lng2: float) -> RouteEdge:
    distance_m = max(300, int(haversine_km(lat1, lng1, lat2, lng2) * 1000))
    walk = max(5, int(distance_m / 1000 / 4 * 60))
    transit = max(10, int(distance_m / 1000 / 15 * 60) + 10)
    drive = max(5, int(distance_m / 1000 / 25 * 60))
    return RouteEdge(
        walk=RouteOption(minutes=walk, distance_meters=distance_m),
        transit=RouteOption(minutes=transit, distance_meters=distance_m),
        drive=RouteOption(minutes=drive, distance_meters=distance_m),
    )


def build_matrix(pois: list[CandidatePoi]) -> dict[tuple[int, int], RouteEdge]:
    matrix: dict[tuple[int, int], RouteEdge] = {}
    coords = {p.poi_id: (p.lat, p.lng) for p in pois}
    coords[-1] = ORIGIN
    ids = list(coords)
    for a in ids:
        for b in ids:
            if a == b:
                continue
            lat1, lng1 = coords[a]
            lat2, lng2 = coords[b]
            matrix[(a, b)] = _edge(lat1, lng1, lat2, lng2)
    return matrix


def make_problem(
    budget: float = 150.0,
    hours: float = 8.0,
    tour_date: date = date(2026, 10, 21),
    interest_tags: list[str] | None = None,
    pinned: list[int] | None = None,
    party_size: int = 1,
    pois: list[CandidatePoi] | None = None,
) -> OptimizeProblem:
    candidates = pois if pois is not None else make_pois()
    return OptimizeProblem(
        candidates=candidates,
        route_matrix=build_matrix(candidates),
        constraints=Constraints(
            tour_date=tour_date,
            budget=budget,
            hours=hours,
            start_minute=9 * 60,
            origin_lat=ORIGIN[0],
            origin_lng=ORIGIN[1],
            origin_name="徐州火车站",
            party_size=party_size,
            pinned_pois=pinned or [],
            interest_tags=interest_tags or [],
        ),
    )