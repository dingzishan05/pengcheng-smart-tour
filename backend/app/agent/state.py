from __future__ import annotations

from datetime import date, datetime
from typing import Literal, TypedDict

from pydantic import BaseModel, Field

from app.optimizer.models import InfeasibilityReport, ItineraryPlan, RouteEdge, WeatherInfo
from app.tools.knowledge import RetrievedChunkInfo
from app.tools.pois import PoiCandidate

IntentType = Literal["itinerary_request", "itinerary_adjust", "knowledge_qa", "invalid_input"]
MissingField = Literal["duration", "budget", "interest", "location", "companions"]
ResponseType = Literal["clarify", "plans", "knowledge_answer", "fallback"]
REQUIRED_FIELDS: tuple[MissingField, ...] = ("duration", "budget")

ORIGIN_KEY_ID = -1


class OriginInfo(BaseModel):
    name: str = ""
    lat: float
    lng: float


class StructuredRequest(BaseModel):
    duration_hours: float | None = None
    budget: float | None = None
    interests: list[str] = Field(default_factory=list)
    origin: OriginInfo | None = None
    companions: str | None = None
    pinned_pois: list[str] = Field(default_factory=list)
    short_trip: bool = False
    visit_date: date | None = None


class MessageRecord(BaseModel):
    role: Literal["user", "system"]
    content: str
    response_type: str | None = None
    sent_at: datetime | None = None


class SourceRef(BaseModel):
    doc_title: str
    source: str


class AgentResponse(BaseModel):
    response_type: ResponseType
    content: str = ""
    plans: list[ItineraryPlan] | None = None
    sources: list[SourceRef] | None = None
    data_time: str | None = None
    defaults_applied: list[str] = Field(default_factory=list)


class AgentError(BaseModel):
    code: str
    message: str


def encode_route_key(from_id: int, to_id: int) -> str:
    return f"{from_id}:{to_id}"


def decode_route_key(key: str) -> tuple[int, int]:
    a, b = key.split(":", 1)
    return int(a), int(b)


def route_matrix_to_optimizer(state_matrix: dict[str, RouteEdge]) -> dict[tuple[int, int], RouteEdge]:
    return {decode_route_key(k): v for k, v in state_matrix.items()}


class TourState(TypedDict, total=False):
    conversation_id: str
    user_message: str
    messages: list[MessageRecord]

    intent: IntentType
    structured_request: StructuredRequest | None
    missing_fields: list[MissingField]
    no_more_questions: bool
    clarify_round: int

    candidate_pois: list[PoiCandidate]
    route_matrix: dict[str, RouteEdge]
    weather: WeatherInfo | None
    visit_date: str

    candidate_plans: list[tuple[int, ...]]
    plans: list[ItineraryPlan] | None
    reference_items: list[int]
    infeasibility: InfeasibilityReport | None

    rag_context: list[RetrievedChunkInfo] | None
    travel_intent_poi: str | None

    final_response: AgentResponse | None
    error: AgentError | None

    defaults_applied: list[str]
    data_degraded: bool
    budget_deadline: float | None