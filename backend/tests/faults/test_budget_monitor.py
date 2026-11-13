"""14.8 图级 30s 预算监视器（design/10 §3.2 / §10.3）。

链路：parse_request 写入 budget_deadline（now + tour_total_budget_seconds）
→ optimize 按剩余时间收紧 CP-SAT 时限（预留 explain 降级时间）
→ explain 剩余不足 6s 时跳过 LLM，模板化解释回退。
"""

from __future__ import annotations

import pytest

from app.agent.nodes.explain import make_explain_node
from app.agent.nodes.optimize import _tuning_with_budget
from app.agent.nodes.parse_request import ParsedIntent, make_parse_request_node
from app.agent.state import AgentResponse
from app.core.config import settings
from app.optimizer.models import (
    DEFAULT_TUNING,
    Goal,
    ItineraryItem as PlanItem,
    ItineraryPlan,
)


def make_plan() -> ItineraryPlan:
    return ItineraryPlan(
        goal=Goal.BALANCED,
        items=[PlanItem(poi_id=1, name="汉文化景区", kind="sight", start_minute=540, end_minute=690, ticket_price=60)],
        segments=[],
        total_cost=60,
        total_minutes=150,
        transit_minutes=0,
    )


async def test_parse_request_writes_budget_deadline():
    async def fake_parse(wrapped_input: str) -> ParsedIntent:
        return ParsedIntent(intent="knowledge_qa")

    base = 1000.0
    node = make_parse_request_node(parse_fn=fake_parse, clock=lambda: base)
    update = await node({"user_message": "龟山汉墓门票多少钱"})
    assert update["budget_deadline"] == pytest.approx(
        base + settings.tour_total_budget_seconds
    )


async def test_explain_template_fallback_when_budget_exhausted():
    llm_calls: list[str] = []

    async def slow_llm(plans_json: str) -> str:
        llm_calls.append(plans_json)
        return "模型说明文本"

    clock_state = {"now": 1000.0}
    node = make_explain_node(explain_fn=slow_llm, clock=lambda: clock_state["now"])

    exhausted = await node({"plans": [make_plan()], "budget_deadline": 1005.0})
    assert llm_calls == []
    assert exhausted["final_response"].response_type == "plans"
    assert exhausted["final_response"].content

    clock_state["now"] = 1000.0
    healthy = await node({"plans": [make_plan()], "budget_deadline": 1030.0})
    assert llm_calls and healthy["final_response"].content == "模型说明文本"


def test_optimize_tightens_solver_time_limit_by_remaining_budget():
    now = 500.0

    ample, remaining = _tuning_with_budget(DEFAULT_TUNING, now + 30.0, lambda: now)
    assert ample.cpsat_time_limit == DEFAULT_TUNING.cpsat_time_limit
    assert remaining == 30.0

    tight, _ = _tuning_with_budget(DEFAULT_TUNING, now + 12.0, lambda: now)
    assert tight.cpsat_time_limit == 6.0

    floor, _ = _tuning_with_budget(DEFAULT_TUNING, now + 3.0, lambda: now)
    assert floor.cpsat_time_limit == 1.0

    untouched, _ = _tuning_with_budget(DEFAULT_TUNING, None, lambda: now)
    assert untouched.cpsat_time_limit == DEFAULT_TUNING.cpsat_time_limit


async def test_explain_without_deadline_still_uses_llm():
    llm_calls: list[str] = []

    async def fast_llm(plans_json: str) -> str:
        llm_calls.append(plans_json)
        return "常规说明"

    node = make_explain_node(explain_fn=fast_llm)
    update = await node({"plans": [make_plan()]})
    assert llm_calls
    assert update["final_response"] is not None


async def test_parse_failure_does_not_block_budget_deadline_pipeline():
    from app.clients.llm import LLMUnavailableError

    async def failing_parse(wrapped_input: str) -> ParsedIntent:
        raise LLMUnavailableError("LLM 超时")

    node = make_parse_request_node(parse_fn=failing_parse, clock=lambda: 1000.0)
    update = await node({"user_message": "帮我规划一天"})
    assert update["error"].code == "50003"
    assert isinstance(update["final_response"], AgentResponse)
    assert "budget_deadline" not in update