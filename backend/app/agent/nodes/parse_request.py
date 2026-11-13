from __future__ import annotations

import time
from datetime import date

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.agent.prompts.parse_request import (
    DIRECT_PLAN_PATTERNS,
    INVALID_INPUT_RESPONSE,
    LLM_FALLBACK_RESPONSE,
    PARSE_PROMPT_VERSION,
    PARSE_SYSTEM_PROMPT,
    PARSE_USER_TEMPLATE,
)
from app.agent.state import (
    AgentError,
    AgentResponse,
    IntentType,
    MessageRecord,
    MissingField,
    OriginInfo,
    StructuredRequest,
    TourState,
)
from app.agent.nodes.common import geocode_origin
from app.clients.llm import LLMUnavailableError, structured_call
from app.core.config import settings
from app.core.logging import get_logger
from app.tools.security import wrap_user_input

logger = get_logger("app.agent.parse_request")

SHORT_TRIP_HOURS = 6.0


class ParsedIntent(BaseModel):
    intent: IntentType
    duration_hours: float | None = None
    budget: float | None = None
    interests: list[str] | None = None
    origin_text: str | None = None
    companions: str | None = None
    pinned_pois: list[str] = Field(default_factory=list)
    visit_date: str | None = None
    direct_plan_requested: bool = False

    @field_validator("pinned_pois", mode="before")
    @classmethod
    def _pinned_pois_null_to_empty(cls, value: object) -> object:
        if value is None:
            return []
        return value


async def default_parse_fn(wrapped_input: str) -> ParsedIntent:
    system = PARSE_SYSTEM_PROMPT.format(today=date.today().isoformat())
    user = PARSE_USER_TEMPLATE.format(wrapped_input=wrapped_input)
    return await structured_call(system, user, ParsedIntent, max_attempts=2)


def _merge_request(base: StructuredRequest | None, parsed: ParsedIntent) -> StructuredRequest:
    merged = base.model_copy(deep=True) if base is not None else StructuredRequest()
    if parsed.duration_hours is not None:
        merged.duration_hours = parsed.duration_hours
    if parsed.budget is not None:
        merged.budget = parsed.budget
    if parsed.interests:
        merged.interests = list(parsed.interests)
    if parsed.origin_text:
        origin = geocode_origin(parsed.origin_text)
        if origin is not None:
            merged.origin = origin
    if parsed.companions is not None:
        merged.companions = parsed.companions
    if parsed.pinned_pois:
        merged.pinned_pois = list(dict.fromkeys([*merged.pinned_pois, *parsed.pinned_pois]))
    if parsed.visit_date:
        try:
            merged.visit_date = date.fromisoformat(parsed.visit_date)
        except ValueError:
            logger.warning("invalid visit_date from LLM", extra={"kind": "algorithm", "value": parsed.visit_date})
    if merged.duration_hours is not None:
        merged.short_trip = merged.duration_hours <= SHORT_TRIP_HOURS
    return merged


def compute_missing_fields(request: StructuredRequest) -> list[MissingField]:
    missing: list[MissingField] = []
    if request.duration_hours is None:
        missing.append("duration")
    if request.budget is None:
        missing.append("budget")
    if not request.interests:
        missing.append("interest")
    if request.origin is None:
        missing.append("location")
    if request.companions is None:
        missing.append("companions")
    return missing


def make_parse_request_node(parse_fn=None, clock=None):
    parse = parse_fn or default_parse_fn
    now = clock or time.monotonic

    async def parse_request_node(state: TourState) -> dict:
        user_message = state.get("user_message", "")
        base_request = state.get("structured_request")
        messages = list(state.get("messages") or [])
        messages.append(MessageRecord(role="user", content=user_message))

        try:
            parsed = await parse(wrap_user_input(user_message))
        except (LLMUnavailableError, ValidationError) as exc:
            logger.error("parse_request LLM failed", extra={"kind": "external", "error": str(exc)[:200]})
            return {
                "messages": messages,
                "error": AgentError(code="50003", message="LLM 服务不可用"),
                "final_response": AgentResponse(response_type="fallback", content=LLM_FALLBACK_RESPONSE),
            }

        if parsed.intent == "invalid_input":
            return {
                "messages": messages,
                "intent": "invalid_input",
                "final_response": AgentResponse(response_type="fallback", content=INVALID_INPUT_RESPONSE),
            }

        merged = _merge_request(base_request, parsed)
        no_more = parsed.direct_plan_requested or any(p in user_message for p in DIRECT_PLAN_PATTERNS)
        missing = compute_missing_fields(merged)
        visit_date = merged.visit_date.isoformat() if merged.visit_date else None

        update: dict = {
            "messages": messages,
            "intent": parsed.intent,
            "structured_request": merged,
            "missing_fields": missing,
            "no_more_questions": no_more,
        }
        if state.get("budget_deadline") is None:
            update["budget_deadline"] = now() + settings.tour_total_budget_seconds
        if visit_date:
            update["visit_date"] = visit_date
        return update

    parse_request_node.__name__ = "parse_request"
    return parse_request_node