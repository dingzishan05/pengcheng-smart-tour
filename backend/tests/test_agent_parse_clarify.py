from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent.nodes.clarify import build_clarify_question, make_ask_clarification_node
from app.agent.nodes.parse_request import (
    ParsedIntent,
    _merge_request,
    compute_missing_fields,
    make_parse_request_node,
)
from app.agent.state import StructuredRequest
from app.clients.llm import LLMUnavailableError, structured_call


def parse_of(**kwargs):
    async def fn(wrapped_input: str) -> ParsedIntent:
        return ParsedIntent(**kwargs)

    return fn


def raiser(exc):
    async def fn(wrapped_input: str):
        raise exc

    return fn


async def test_parse_full_request_no_missing():
    node = make_parse_request_node(
        parse_of(intent="itinerary_request", duration_hours=8, budget=300, interests=["历史文化"])
    )
    update = await node({"user_message": "我第一次来徐州，只有一天，预算300元，喜欢历史文化"})
    assert update["intent"] == "itinerary_request"
    request: StructuredRequest = update["structured_request"]
    assert request.duration_hours == 8
    assert request.budget == 300
    assert request.interests == ["历史文化"]
    assert request.short_trip is False
    assert "duration" not in update["missing_fields"]
    assert "budget" not in update["missing_fields"]


async def test_parse_short_trip_flag():
    node = make_parse_request_node(
        parse_of(intent="itinerary_request", duration_hours=4, budget=100)
    )
    update = await node({"user_message": "我只有4个小时，想去2个地方"})
    assert update["structured_request"].short_trip is True


async def test_parse_merge_budget_only():
    base = StructuredRequest(duration_hours=8, budget=300, interests=["历史文化"])
    node = make_parse_request_node(
        parse_of(intent="itinerary_request", budget=200, interests=None)
    )
    update = await node(
        {
            "user_message": "太贵了，改成200",
            "structured_request": base,
        }
    )
    request: StructuredRequest = update["structured_request"]
    assert request.budget == 200
    assert request.duration_hours == 8
    assert request.interests == ["历史文化"]


async def test_parse_no_more_questions():
    node = make_parse_request_node(
        parse_of(intent="itinerary_request", budget=300, direct_plan_requested=True)
    )
    update = await node({"user_message": "别问了，按默认来，300元一天"})
    assert update["no_more_questions"] is True
    assert "duration" in update["missing_fields"]


async def test_parse_no_more_questions_from_text():
    node = make_parse_request_node(parse_of(intent="itinerary_request", budget=300))
    update = await node({"user_message": "别问了，按默认来，300元一天"})
    assert update["no_more_questions"] is True


async def test_parse_llm_failure_fallback():
    node = make_parse_request_node(raiser(LLMUnavailableError("boom")))
    update = await node({"user_message": "帮我安排徐州一日游"})
    assert update["error"].code == "50003"
    assert update["final_response"].response_type == "fallback"
    assert "服务繁忙" in update["final_response"].content
    assert update["messages"][0].role == "user"
    assert update["messages"][0].content == "帮我安排徐州一日游"


async def test_parse_invalid_input_guidance():
    node = make_parse_request_node(parse_of(intent="invalid_input"))
    update = await node({"user_message": "@@@???"})
    assert update["intent"] == "invalid_input"
    assert update["final_response"].response_type == "fallback"
    assert "没理解" in update["final_response"].content


async def test_parse_pinned_pois_accumulate():
    base = StructuredRequest(duration_hours=8, budget=300, pinned_pois=["龟山汉墓"])
    node = make_parse_request_node(
        parse_of(intent="itinerary_request", pinned_pois=["云龙湖", "龟山汉墓"])
    )
    update = await node({"user_message": "再加个云龙湖", "structured_request": base})
    assert update["structured_request"].pinned_pois == ["龟山汉墓", "云龙湖"]


def test_missing_fields_all_present():
    request = StructuredRequest(
        duration_hours=8, budget=300, interests=["历史"],
        origin=None, companions=None,
    )
    missing = compute_missing_fields(request)
    assert "duration" not in missing
    assert "budget" not in missing
    assert "interest" not in missing
    assert "location" in missing
    assert "companions" in missing


async def test_clarify_asks_all_missing_at_once():
    node = make_ask_clarification_node()
    update = await node({"missing_fields": ["duration", "budget", "interest", "location"]})
    content = update["final_response"].content
    assert "多长" in content
    assert "预算" in content
    assert content.count("？") <= 4
    assert update["clarify_round"] == 1
    assert update["final_response"].response_type == "clarify"


async def test_clarify_round_increments():
    node = make_ask_clarification_node()
    update = await node({"missing_fields": ["duration"], "clarify_round": 1})
    assert update["clarify_round"] == 2


def test_build_clarify_question_required_first():
    question = build_clarify_question(["location", "interest", "budget", "duration"])
    assert question.index("多长") < question.index("主题")
    assert question.count("？") <= 4


@pytest.mark.parametrize(
    "question",
    ["帮我安排一下徐州游玩", "随便帮我规划下"],
)
def test_clarify_question_contains_budget_and_duration(question):
    result = build_clarify_question(["duration", "budget"])
    assert "多长" in result and "预算" in result

def test_parsed_intent_pinned_pois_null_normalized():
    parsed = ParsedIntent.model_validate({"intent": "itinerary_request", "pinned_pois": None})
    assert parsed.pinned_pois == []


def test_parsed_intent_pinned_pois_missing_equals_null():
    with_null = ParsedIntent.model_validate({"intent": "itinerary_request", "pinned_pois": None})
    missing = ParsedIntent.model_validate({"intent": "itinerary_request"})
    assert with_null.pinned_pois == missing.pinned_pois == []


def test_parsed_intent_pinned_pois_invalid_element_rejected():
    with pytest.raises(ValidationError):
        ParsedIntent.model_validate({"intent": "itinerary_request", "pinned_pois": [123]})


def test_merge_request_null_pinned_keeps_existing():
    base = StructuredRequest(duration_hours=8, budget=300, pinned_pois=["云龙湖"])
    parsed = ParsedIntent.model_validate({"intent": "itinerary_request", "pinned_pois": None})
    merged = _merge_request(base, parsed)
    assert merged.pinned_pois == ["云龙湖"]


async def test_structured_call_null_pinned_single_attempt(monkeypatch):
    calls: list[list[dict]] = []

    async def fake_post(messages, temperature=0.3):
        calls.append(messages)
        content = '{"intent": "itinerary_request", "duration_hours": 8, "pinned_pois": null}'
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr("app.clients.llm._post_chat", fake_post)
    result = await structured_call("sys", "user", ParsedIntent)
    assert isinstance(result, ParsedIntent)
    assert result.pinned_pois == []
    assert len(calls) == 1