from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END

from app.agent.graph import build_graph, route_after_generate, route_after_parse, route_after_rag
from app.agent.nodes.parse_request import ParsedIntent
from app.agent.state import StructuredRequest
from app.clients.llm import LLMUnavailableError
from app.rag.generator import GenerateResult
from app.tools.base import ToolErrorKind, ToolMeta, ToolResult
from app.tools.knowledge import KnowledgeRetrievalOutput, RetrievedChunkInfo
from app.tools.live import RouteOptionInfo, WeatherSnapshot
from app.tools.pois import Coordinate, PoiCandidate, TimeWindow


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def fake_session_factory():
    return FakeSession()


def parse_of(**kwargs):
    async def fn(wrapped_input: str) -> ParsedIntent:
        return ParsedIntent(**kwargs)

    return fn


def make_poi(poi_id, name, price=0.0, visit_minutes=90, tags=None):
    return PoiCandidate(
        poi_id=poi_id,
        name=name,
        category="景区",
        tags=tags or ["历史"],
        location=Coordinate(lat=34.25, lng=117.18),
        visit_minutes=visit_minutes,
        ticket_price=price,
        open_window=TimeWindow(open_minute=540, close_minute=1080),
        interest_score=0.8,
        indoor=True,
    )


def patch_itinerary_env(monkeypatch, pois):
    async def fake_search(session, query):
        kept = [
            p for p in pois
            if not query.budget_cap or p.ticket_price <= float(query.budget_cap)
        ]
        return ToolResult[list[PoiCandidate]].success(kept, ToolMeta(source_channel="test"))

    async def fake_route(origin, destination, mode):
        return ToolResult.success(
            RouteOptionInfo(mode=mode, duration_minutes=15, distance_meters=1200),
            ToolMeta(source_channel="test"),
        )

    async def fake_weather(city, visit_date):
        return ToolResult.success(
            WeatherSnapshot(condition="晴", temperature="24"),
            ToolMeta(source_channel="test"),
        )

    async def fake_cache_get(key):
        return None

    monkeypatch.setattr("app.agent.nodes.retrieve_pois.search_attractions", fake_search)
    monkeypatch.setattr("app.agent.nodes.fetch_live.get_route", fake_route)
    monkeypatch.setattr("app.agent.nodes.fetch_live.get_weather", fake_weather)
    monkeypatch.setattr("app.agent.nodes.fetch_live.cache_get", fake_cache_get)


def safe_explain_fn(plans_json: str):
    async def fn(payload):
        return "已按您的时长、预算与兴趣安排行程，兼顾费用与时间，详情见方案卡片。"

    return fn


FULL_PARSE = dict(
    intent="itinerary_request",
    duration_hours=8,
    budget=300,
    interests=["历史文化"],
    origin_text="徐州东站",
)


async def test_graph_full_itinerary_flow(monkeypatch):
    patch_itinerary_env(
        monkeypatch,
        [
            make_poi(1, "徐州博物馆"),
            make_poi(2, "户部山古民居", price=30),
            make_poi(3, "云龙湖", tags=["自然风光"]),
        ],
    )
    app = build_graph(
        fake_session_factory,
        parse_fn=parse_of(**FULL_PARSE),
        explain_fn=safe_explain_fn(""),
    )
    result = await app.ainvoke({"user_message": "我第一次来徐州，只有一天，预算300元，喜欢历史文化"})
    response = result["final_response"]
    assert response.response_type == "plans"
    assert response.plans
    assert len(response.plans) <= 3
    for plan in response.plans:
        assert plan.total_cost <= 300 + 1e-6
    assert response.content


async def test_graph_clarify_then_complete_with_checkpointer(monkeypatch):
    patch_itinerary_env(
        monkeypatch,
        [make_poi(1, "徐州博物馆"), make_poi(2, "户部山古民居", price=30)],
    )
    calls = {"n": 0}

    async def two_phase_parse(wrapped_input):
        calls["n"] += 1
        if calls["n"] == 1:
            return ParsedIntent(intent="itinerary_request")
        return ParsedIntent(
            intent="itinerary_request", duration_hours=8, budget=300, interests=["历史文化"]
        )

    saver = InMemorySaver()
    app = build_graph(fake_session_factory, parse_fn=two_phase_parse, explain_fn=safe_explain_fn(""), checkpointer=saver)
    config = {"configurable": {"thread_id": "conv-1"}}

    first = await app.ainvoke({"user_message": "帮我安排一下徐州游玩"}, config)
    assert first["final_response"].response_type == "clarify"
    assert first["clarify_round"] == 1
    assert "多长" in first["final_response"].content
    assert "预算" in first["final_response"].content

    second = await app.ainvoke({"user_message": "一天，预算300元，喜欢历史文化"}, config)
    assert second["final_response"].response_type == "plans"
    assert second["clarify_round"] == 1
    assert second["structured_request"].budget == 300


async def test_graph_no_more_questions_uses_defaults(monkeypatch):
    patch_itinerary_env(monkeypatch, [make_poi(1, "徐州博物馆"), make_poi(2, "户部山古民居", price=30)])
    app = build_graph(
        fake_session_factory,
        parse_fn=parse_of(intent="itinerary_request", budget=300, direct_plan_requested=True),
        explain_fn=safe_explain_fn(""),
    )
    result = await app.ainvoke({"user_message": "别问了，按默认来，300元一天"})
    response = result["final_response"]
    assert response.response_type == "plans"
    assert result["defaults_applied"]
    assert "时长" in "；".join(result["defaults_applied"])
    assert result["structured_request"].duration_hours == 4.0


async def test_graph_invalid_input_ends(monkeypatch):
    app = build_graph(fake_session_factory, parse_fn=parse_of(intent="invalid_input"))
    result = await app.ainvoke({"user_message": "!!!???~~~"})
    assert result["final_response"].response_type == "fallback"
    assert "没理解" in result["final_response"].content
    assert result.get("plans") is None


async def test_graph_itinerary_adjust_routes_to_end(monkeypatch):
    app = build_graph(
        fake_session_factory,
        parse_fn=parse_of(intent="itinerary_adjust", budget=150),
    )
    result = await app.ainvoke({"user_message": "预算改成150"})
    assert result["intent"] == "itinerary_adjust"
    assert result.get("final_response") is None


async def test_graph_empty_candidates_infeasible(monkeypatch):
    patch_itinerary_env(monkeypatch, [])
    app = build_graph(
        fake_session_factory,
        parse_fn=parse_of(**FULL_PARSE),
        explain_fn=safe_explain_fn(""),
    )
    result = await app.ainvoke({"user_message": "想去冷门景点"})
    assert result["final_response"].response_type == "fallback"
    assert result["infeasibility"] is not None
    assert result["infeasibility"].reasons


async def test_graph_r6_no_feasible_sequence(monkeypatch):
    patch_itinerary_env(
        monkeypatch,
        [make_poi(1, "超长景区", visit_minutes=600), make_poi(2, "另一个超长景区", visit_minutes=600)],
    )
    app = build_graph(
        fake_session_factory,
        parse_fn=parse_of(**FULL_PARSE),
        explain_fn=safe_explain_fn(""),
    )
    result = await app.ainvoke({"user_message": "一天预算300元"})
    assert result["final_response"].response_type == "fallback"
    assert result["infeasibility"] is not None
    assert result.get("candidate_plans") == []


def patch_rag_env(monkeypatch, chunks, insufficient=False, low_confidence=False, answer="参考答案[1]"):
    output = KnowledgeRetrievalOutput(
        chunks=chunks, insufficient=insufficient, low_confidence=low_confidence
    )

    async def fake_retrieve(session, query):
        return ToolResult.success(output, ToolMeta(source_channel="test"))

    class FakeRepo:
        def __init__(self, session):
            pass

        async def list_active(self):
            return [SimpleNamespace(name="龟山汉墓"), SimpleNamespace(name="云龙湖")]

    async def fake_answer(query, retrieval, structured_facts=None):
        return GenerateResult(answer=answer, citations=[1])

    monkeypatch.setattr("app.agent.nodes.rag_qa.retrieve_xuzhou_knowledge", fake_retrieve)
    monkeypatch.setattr("app.agent.nodes.rag_qa.PoiRepository", FakeRepo)
    monkeypatch.setattr("app.agent.nodes.rag_qa.generate_answer", fake_answer)


def sample_chunks():
    return [
        RetrievedChunkInfo(
            chunk_id=1,
            doc_title="龟山汉墓游玩攻略",
            topic="景点",
            source="徐州市文旅局",
            chunk_text="龟山汉墓是西汉楚王襄王刘注的夫妻合葬墓。",
            similarity=0.86,
        )
    ]


async def test_graph_knowledge_qa_hit(monkeypatch):
    patch_rag_env(monkeypatch, sample_chunks())
    app = build_graph(fake_session_factory, parse_fn=parse_of(intent="knowledge_qa"))
    result = await app.ainvoke({"user_message": "龟山汉墓是什么"})
    response = result["final_response"]
    assert response.response_type == "knowledge_answer"
    assert response.sources and response.sources[0].doc_title == "龟山汉墓游玩攻略"


async def test_graph_knowledge_qa_refusal(monkeypatch):
    patch_rag_env(monkeypatch, [], insufficient=True)
    app = build_graph(fake_session_factory, parse_fn=parse_of(intent="knowledge_qa"))
    result = await app.ainvoke({"user_message": "量子力学在徐州的应用"})
    response = result["final_response"]
    assert response.response_type == "fallback"
    assert "可靠资料" in response.content


async def test_graph_travel_intent_bridge_to_itinerary(monkeypatch):
    patch_itinerary_env(
        monkeypatch,
        [make_poi(1, "龟山汉墓", price=60), make_poi(2, "徐州博物馆")],
    )
    patch_rag_env(monkeypatch, sample_chunks(), answer="龟山汉墓值得游览[1]")

    app = build_graph(
        fake_session_factory,
        parse_fn=parse_of(
            intent="knowledge_qa", duration_hours=8, budget=300, interests=["历史文化"]
        ),
        explain_fn=safe_explain_fn(""),
    )
    result = await app.ainvoke({"user_message": "那就去龟山汉墓吧，帮我安排一天"})
    assert result["travel_intent_poi"] == "龟山汉墓"
    assert "龟山汉墓" in result["structured_request"].pinned_pois
    response = result["final_response"]
    assert response.response_type == "plans"
    pinned_included = any(
        any(item.name == "龟山汉墓" for item in plan.items) for plan in response.plans
    )
    assert pinned_included


async def test_explain_number_guard_strips_fabricated_numbers(monkeypatch):
    patch_itinerary_env(
        monkeypatch, [make_poi(1, "徐州博物馆"), make_poi(2, "户部山古民居", price=30)]
    )

    async def hallucinating_explain(plans_json):
        return "本方案总费用约 9999 元，超级划算。"

    app = build_graph(
        fake_session_factory, parse_fn=parse_of(**FULL_PARSE), explain_fn=hallucinating_explain
    )
    result = await app.ainvoke({"user_message": "一天300元历史文化"})
    assert "9999" not in result["final_response"].content


async def test_explain_time_budget_falls_back_to_template(monkeypatch):
    patch_itinerary_env(
        monkeypatch, [make_poi(1, "徐州博物馆"), make_poi(2, "户部山古民居", price=30)]
    )
    calls = {"n": 0}

    async def tracked_explain(plans_json):
        calls["n"] += 1
        return "不应被调用"

    app = build_graph(
        fake_session_factory,
        parse_fn=parse_of(**FULL_PARSE),
        explain_fn=tracked_explain,
        clock=time.monotonic,
    )
    result = await app.ainvoke(
        {"user_message": "一天300元历史文化", "budget_deadline": time.monotonic() - 1}
    )
    assert calls["n"] == 0
    assert "省钱" in result["final_response"].content


def test_route_truth_table_r1_to_r5():
    assert route_after_parse({"intent": "knowledge_qa"}) == "rag_qa"
    assert route_after_parse({"intent": "invalid_input"}) == END
    assert route_after_parse({"intent": "itinerary_adjust"}) == END
    assert route_after_parse({"error": {"code": "50003"}}) == END
    assert (
        route_after_parse(
            {"intent": "itinerary_request", "missing_fields": ["duration"], "clarify_round": 0}
        )
        == "ask_clarification"
    )
    assert (
        route_after_parse(
            {"intent": "itinerary_request", "missing_fields": ["duration", "budget"], "clarify_round": 1}
        )
        == "ask_clarification"
    )
    assert (
        route_after_parse(
            {"intent": "itinerary_request", "missing_fields": ["duration"], "clarify_round": 2}
        )
        == "retrieve_pois"
    )
    assert (
        route_after_parse(
            {
                "intent": "itinerary_request",
                "missing_fields": ["duration"],
                "clarify_round": 0,
                "no_more_questions": True,
            }
        )
        == "retrieve_pois"
    )
    assert (
        route_after_parse({"intent": "itinerary_request", "missing_fields": ["interest"]})
        == "retrieve_pois"
    )


def test_route_truth_table_r6():
    assert route_after_generate({"candidate_plans": []}) == END
    assert route_after_generate({"candidate_plans": [(1,), (1, 2)]}) == "optimize"
    assert route_after_generate({"error": {"code": "50006"}}) == END


def test_route_truth_table_rag_branches():
    assert route_after_rag({}) == END
    assert route_after_rag({"travel_intent_poi": "龟山汉墓"}) == "retrieve_pois"
    assert (
        route_after_rag(
            {"travel_intent_poi": "龟山汉墓", "missing_fields": ["duration"], "clarify_round": 0}
        )
        == "ask_clarification"
    )
    assert route_after_rag({"error": {"code": "50002"}}) == END

async def test_graph_clarify_round_limit_falls_back_to_defaults(monkeypatch):
    patch_itinerary_env(
        monkeypatch,
        [make_poi(1, "徐州博物馆"), make_poi(2, "户部山古民居", price=30)],
    )

    async def always_missing(wrapped_input):
        return ParsedIntent(intent="itinerary_request")

    saver = InMemorySaver()
    app = build_graph(
        fake_session_factory,
        parse_fn=always_missing,
        explain_fn=safe_explain_fn(""),
        checkpointer=saver,
    )
    config = {"configurable": {"thread_id": "conv-round-limit"}}

    first = await app.ainvoke({"user_message": "想去徐州玩"}, config)
    assert first["final_response"].response_type == "clarify"
    assert first["clarify_round"] == 1

    second = await app.ainvoke({"user_message": "还没想好"}, config)
    assert second["final_response"].response_type == "clarify"
    assert second["clarify_round"] == 2

    third = await app.ainvoke({"user_message": "仍然不知道"}, config)
    response = third["final_response"]
    assert response.response_type == "plans"
    assert third["clarify_round"] == 2
    joined = "；".join(third["defaults_applied"])
    assert "时长" in joined
    assert "预算" in joined
    assert third["structured_request"].duration_hours == 4.0
    assert third["structured_request"].budget == 500


async def test_graph_route_tool_failure_degrades_with_notice(monkeypatch):
    pois = [make_poi(1, "徐州博物馆"), make_poi(2, "户部山古民居", price=30)]

    async def fake_search(session, query):
        return ToolResult[list[PoiCandidate]].success(pois, ToolMeta(source_channel="test"))

    async def flaky_route(origin, destination, mode):
        if mode == "公交":
            return ToolResult.failure(ToolErrorKind.EXTERNAL, "LIVE_ROUTE_FAIL", "路线服务暂不可用")
        return ToolResult.success(
            RouteOptionInfo(mode=mode, duration_minutes=15, distance_meters=1200),
            ToolMeta(source_channel="test"),
        )

    async def failed_weather(city, visit_date):
        return ToolResult.failure(ToolErrorKind.EXTERNAL, "LIVE_WEATHER_FAIL", "天气服务暂不可用")

    async def fake_cache_get(key):
        return None

    monkeypatch.setattr("app.agent.nodes.retrieve_pois.search_attractions", fake_search)
    monkeypatch.setattr("app.agent.nodes.fetch_live.get_route", flaky_route)
    monkeypatch.setattr("app.agent.nodes.fetch_live.get_weather", failed_weather)
    monkeypatch.setattr("app.agent.nodes.fetch_live.cache_get", fake_cache_get)

    app = build_graph(
        fake_session_factory,
        parse_fn=parse_of(**FULL_PARSE),
        explain_fn=safe_explain_fn(""),
    )
    result = await app.ainvoke({"user_message": "一天300元历史文化"})
    response = result["final_response"]
    assert response.response_type == "plans"
    assert response.plans
    assert result["data_degraded"] is True
    assert response.data_time is not None
    assert "降级" in response.data_time


async def test_explain_llm_unavailable_falls_back_to_template(monkeypatch):
    patch_itinerary_env(
        monkeypatch, [make_poi(1, "徐州博物馆"), make_poi(2, "户部山古民居", price=30)]
    )

    async def failing_explain(plans_json):
        raise LLMUnavailableError("llm down")

    app = build_graph(
        fake_session_factory, parse_fn=parse_of(**FULL_PARSE), explain_fn=failing_explain
    )
    result = await app.ainvoke({"user_message": "一天300元历史文化"})
    response = result["final_response"]
    assert response.response_type == "plans"
    assert response.plans
    assert "省钱" in response.content