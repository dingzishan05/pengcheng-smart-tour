from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Self

from pydantic import BaseModel, Field

from app.core.config import settings

ORIGIN_ID = -1
MINUTES_PER_DAY = 24 * 60
LUNCH_START = 11 * 60 + 30
LUNCH_END = 13 * 60 + 30
WEEKDAY_MONDAY = 0


class TransportMode(str, Enum):
    WALK = "步行"
    TRANSIT = "公交"
    DRIVE = "驾车"


class Goal(str, Enum):
    SAVE_MONEY = "save_money"
    SAVE_TIME = "save_time"
    BALANCED = "balanced"


class RouteOption(BaseModel):
    minutes: int = Field(ge=0)
    distance_meters: int = Field(ge=0)


class RouteEdge(BaseModel):
    walk: RouteOption | None = None
    transit: RouteOption | None = None
    drive: RouteOption | None = None

    def option(self, mode: TransportMode) -> RouteOption | None:
        field_name = {
            TransportMode.WALK: "walk",
            TransportMode.TRANSIT: "transit",
            TransportMode.DRIVE: "drive",
        }[mode]
        return getattr(self, field_name)

    def available_modes(self) -> list[TransportMode]:
        return [m for m in TransportMode if self.option(m) is not None]


class CandidatePoi(BaseModel):
    poi_id: int
    name: str
    visit_minutes: int = Field(gt=0)
    ticket_price: float = Field(default=0, ge=0)
    is_reference_price: bool = False
    interest_score: float = Field(default=0.5, ge=0, le=1)
    tags: list[str] = Field(default_factory=list)
    lat: float
    lng: float
    indoor: bool = True
    status_active: bool = True
    open_windows: dict[int, tuple[int, int]] = Field(default_factory=dict)

    def window_on(self, weekday: int) -> tuple[int, int] | None:
        return self.open_windows.get(weekday)

    def is_free(self) -> bool:
        return self.ticket_price <= 0


class WeatherInfo(BaseModel):
    condition_text: str = ""
    has_heavy_rain: bool = False
    has_strong_wind: bool = False

    @property
    def outdoor_risky(self) -> bool:
        return self.has_heavy_rain or self.has_strong_wind


class Constraints(BaseModel):
    tour_date: date
    budget: float = Field(ge=0)
    hours: float = Field(gt=0)
    start_minute: int = Field(default=9 * 60, ge=0, le=MINUTES_PER_DAY)
    origin_name: str = "出发地"
    origin_lat: float
    origin_lng: float
    party_size: int = Field(default=1, ge=1)
    pinned_pois: list[int] = Field(default_factory=list)
    interest_tags: list[str] = Field(default_factory=list)

    @property
    def end_minute_limit(self) -> int:
        return self.start_minute + int(self.hours * 60)

    @property
    def weekday(self) -> int:
        return self.tour_date.weekday()


class OptimizeProblem(BaseModel):
    candidates: list[CandidatePoi]
    route_matrix: dict[tuple[int, int], RouteEdge] = Field(default_factory=dict)
    constraints: Constraints
    weather: WeatherInfo | None = None

    def edge(self, from_poi_id: int, to_poi_id: int) -> RouteEdge | None:
        return self.route_matrix.get((from_poi_id, to_poi_id))

    def poi(self, poi_id: int) -> CandidatePoi | None:
        for c in self.candidates:
            if c.poi_id == poi_id:
                return c
        return None

    def max_visits(self) -> int:
        if self.constraints.hours > 6:
            return 8
        # 短时游按约 2 小时/景点估算上限（交通与用餐由求解器再约束），
        # 4 小时至少能安排 2 个点，避免“4 小时只能去 1 个地方”。
        return max(1, int(self.constraints.hours * 60 // 120))


class ItineraryItem(BaseModel):
    poi_id: int
    name: str
    kind: str = "sight"
    start_minute: int
    end_minute: int
    ticket_price: float = 0
    is_reference_price: bool = False
    interest_score: float = 0.5
    tags: list[str] = Field(default_factory=list)


class TransportSegment(BaseModel):
    from_poi_id: int
    to_poi_id: int
    mode: TransportMode
    minutes: int
    fare: float = 0
    distance_meters: int = 0


class ItineraryPlan(BaseModel):
    goal: Goal
    items: list[ItineraryItem] = Field(default_factory=list)
    segments: list[TransportSegment] = Field(default_factory=list)
    total_cost: float = 0
    ticket_cost: float = 0
    transit_cost: float = 0
    meal_cost: float = 0
    transit_minutes: int = 0
    total_minutes: int = 0
    interest_score: float = 0
    solver_note: str | None = None
    similar_note: str | None = None

    def poi_ids(self) -> list[int]:
        return [i.poi_id for i in self.items if i.kind == "sight"]

    def has_lunch(self) -> bool:
        return any(i.kind == "meal" for i in self.items)


class PlanBundle(BaseModel):
    save_money: ItineraryPlan | None = None
    save_time: ItineraryPlan | None = None
    balanced: ItineraryPlan | None = None


class InfeasibleReasonKind(str, Enum):
    BUDGET = "budget"
    DURATION = "duration"
    CLOSED = "closed"
    UNREACHABLE = "unreachable"


class InfeasibleReason(BaseModel):
    kind: InfeasibleReasonKind
    detail: str


class InfeasibilityAdvice(BaseModel):
    min_budget_suggestion: str | None = None
    free_first: bool = False
    duration_suggestion: str | None = None


class InfeasibilityReport(BaseModel):
    reasons: list[InfeasibleReason] = Field(default_factory=list)
    advice: InfeasibilityAdvice = Field(default_factory=InfeasibilityAdvice)


class PlanDiff(BaseModel):
    removed_poi_ids: list[int] = Field(default_factory=list)
    added_poi_ids: list[int] = Field(default_factory=list)
    cost_delta: float = 0
    detail: str = ""


class ReplanResult(BaseModel):
    new_plan: ItineraryPlan
    diff: PlanDiff
    reason: str
    pending: bool = True


@dataclass
class TuningParams:
    transit_fare: float = 2.0
    drive_fare_per_km: float = 0.8
    buffer_ratio: float = 1.15
    meal_minutes: int = 45
    meal_tier_low: int = 30
    meal_tier_mid: int = 50
    meal_tier_high: int = 80
    meal_budget_low: float = 150.0
    meal_budget_mid: float = 300.0
    short_hours: float = 6.0
    short_radius_km: float = 10.0
    min_tag_pool: int = 4
    enum_threshold: int = 5000
    secondary_pool_size: int = 20
    dedup_threshold: float = 0.8
    jaccard_weight: float = 0.6
    kendall_weight: float = 0.4
    money_tolerance: float = 0.05
    balanced_cost_weight: float = 0.5
    balanced_time_weight: float = 0.5
    balanced_interest_weight: float = 0.2
    repair_slack_minutes: int = 5
    cpsat_time_limit: float = field(default_factory=lambda: float(settings.optimizer_time_limit))
    top_k: int = field(default_factory=lambda: settings.optimizer_max_candidates)
    pool_limit: int = field(default_factory=lambda: settings.optimizer_candidate_pool_limit)

    def meal_tier(self, budget: float) -> int:
        if budget < self.meal_budget_low:
            return self.meal_tier_low
        if budget <= self.meal_budget_mid:
            return self.meal_tier_mid
        return self.meal_tier_high

    def buffered_minutes(self, minutes: int) -> int:
        return int(math.ceil(minutes * self.buffer_ratio))


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def mode_fare(mode: TransportMode, edge: RouteEdge, party_size: int, tuning: TuningParams) -> float:
    option = edge.option(mode)
    if option is None:
        raise ValueError(f"mode {mode} unavailable on edge")
    if mode == TransportMode.WALK:
        return 0.0
    if mode == TransportMode.TRANSIT:
        return tuning.transit_fare * party_size
    return round(option.distance_meters / 1000 * tuning.drive_fare_per_km, 2)


DEFAULT_TUNING = TuningParams()