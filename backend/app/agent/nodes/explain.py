from __future__ import annotations

import asyncio
import json
import re
import time

from app.agent.prompts.explain import (
    CLOSED_AVOIDED_NOTE,
    EXPLAIN_SYSTEM_PROMPT,
    EXPLAIN_USER_TEMPLATE,
    REFERENCE_NOTE,
    TEMPLATE_EXPLANATION,
)
from app.agent.state import AgentResponse, TourState
from app.clients.llm import LLMUnavailableError, text_call
from app.core.config import settings
from app.core.logging import get_logger
from app.optimizer.models import ItineraryPlan, WEEKDAY_MONDAY

logger = get_logger("app.agent.explain")

EXPLAIN_TIME_RESERVE_SECONDS = 6.0
EXPLAIN_MAX_SECONDS = 12.0
GOAL_NAMES = {"save_money": "省钱", "save_time": "省时", "balanced": "均衡"}
NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")


async def default_explain_fn(plans_json: str) -> str:
    return await text_call(
        EXPLAIN_SYSTEM_PROMPT,
        EXPLAIN_USER_TEMPLATE.format(plans_json=plans_json),
        temperature=0.2,
    )


def plans_payload(plans: list[ItineraryPlan], reference_items: list[int], weekday: int | None) -> dict:
    return {
        "plans": [
            {
                "goal": GOAL_NAMES.get(p.goal.value, p.goal.value),
                "total_cost": round(p.total_cost, 2),
                "total_minutes": p.total_minutes,
                "transport_minutes": p.transit_minutes,
                "sequence": [i.name for i in p.items if i.kind == "sight"],
                "has_lunch": p.has_lunch(),
                "solver_note": p.solver_note,
            }
            for p in plans
        ],
        "reference_items": reference_items,
        "closed_note": CLOSED_AVOIDED_NOTE if weekday == WEEKDAY_MONDAY else None,
        "visit_weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][weekday]
        if weekday is not None
        else None,
    }


def _normalize_number(match: str) -> str:
    if "." in match:
        return match.rstrip("0").rstrip(".") or "0"
    return match


def collect_numbers(payload: dict) -> set[str]:
    allowed: set[str] = set()
    for match in NUMBER_PATTERN.findall(json.dumps(payload, ensure_ascii=False)):
        allowed.add(_normalize_number(match))
    allowed.add(str(len(payload.get("plans") or [])))
    return allowed


def check_numbers(text: str, allowed: set[str]) -> bool:
    for match in NUMBER_PATTERN.findall(text):
        if _normalize_number(match) in allowed:
            continue
        return False
    return True


def strip_unknown_number_sentences(text: str, allowed: set[str]) -> str:
    sentences = re.split(r"(?<=[。！？\n])", text)
    kept = [
        s for s in sentences
        if check_numbers(s, allowed)
    ]
    result = "".join(kept).strip()
    return result or "方案说明请参见行程详情。"


def build_template_explanation(plans: list[ItineraryPlan], reference_needed: bool, weekday: int | None) -> str:
    if not plans:
        return "未能生成行程方案，请调整需求后重试。"
    money = next((p for p in plans if p.goal.value == "save_money"), plans[0])
    fastest = next((p for p in plans if p.goal.value == "save_time"), plans[0])
    closed_note = CLOSED_AVOIDED_NOTE if weekday == WEEKDAY_MONDAY else ""
    reference_note = REFERENCE_NOTE if reference_needed else ""
    return TEMPLATE_EXPLANATION.format(
        plan_count=len(plans),
        plan_names="、".join(GOAL_NAMES.get(p.goal.value, p.goal.value) for p in plans),
        money_cost=round(money.total_cost, 2),
        time_cost=fastest.transit_minutes,
        closed_note=closed_note,
        reference_note=reference_note,
    )


def make_explain_node(explain_fn=None, clock=time.monotonic):
    explain = explain_fn or default_explain_fn
    now = clock or time.monotonic

    def _weekday_of(state: TourState) -> int | None:
        request = state.get("structured_request")
        if request is not None and getattr(request, "visit_date", None) is not None:
            return request.visit_date.weekday()
        raw = state.get("visit_date")
        if raw:
            try:
                from datetime import date as _date

                return _date.fromisoformat(raw).weekday()
            except ValueError:
                return None
        return None

    async def explain_node(state: TourState) -> dict:
        plans: list[ItineraryPlan] = list(state.get("plans") or [])
        defaults_applied = list(state.get("defaults_applied") or [])
        reference_items = list(state.get("reference_items") or [])
        deadline = state.get("budget_deadline")
        degraded = bool(state.get("data_degraded"))

        if not plans:
            infeasibility = state.get("infeasibility")
            if infeasibility is not None:
                return {"final_response": AgentResponse(
                    response_type="fallback",
                    content="当前条件下暂时无法生成可行行程，请尝试调整预算、时长或兴趣主题。",
                )}
            return {"final_response": AgentResponse(
                response_type="fallback",
                content="未能生成行程方案，请稍后重试。",
            )}

        payload = plans_payload(plans, reference_items, _weekday_of(state))
        plans_json = json.dumps(payload, ensure_ascii=False)
        allowed = collect_numbers(payload)

        remaining = None
        if deadline is not None:
            remaining = deadline - now()

        explanation = ""
        # 解释只是“锦上添花”：最多等 12 秒，超时就用模板话术，保证 30 秒内出结果。
        llm_budget = min(float(settings.llm_timeout_seconds), EXPLAIN_MAX_SECONDS)
        if remaining is not None:
            if remaining < EXPLAIN_TIME_RESERVE_SECONDS:
                llm_budget = 0.0
            else:
                llm_budget = min(llm_budget, remaining - 2.0)
        if llm_budget >= 2.0:
            try:
                text = await asyncio.wait_for(explain(plans_json), timeout=llm_budget)
                if check_numbers(text, allowed):
                    explanation = text
                else:
                    # 数字不合规时不再二次调用大模型：既拖慢响应也增加费用。
                    # 剔除后若只剩残句（如“3.”），直接退回模板以免输出半截内容。
                    stripped = strip_unknown_number_sentences(text, allowed)
                    if len(stripped) >= max(40, int(len(text) * 0.6)):
                        explanation = stripped
            except (LLMUnavailableError, asyncio.TimeoutError) as exc:
                logger.warning(
                    "explain LLM failed, fallback to template",
                    extra={"kind": "external", "error": str(exc)[:200]},
                )
                explanation = ""

        if not explanation:
            explanation = build_template_explanation(
                plans, bool(reference_items), _weekday_of(state)
            )

        if defaults_applied:
            explanation += "\n（" + "；".join(defaults_applied) + "）"
        data_time = "部分路线/天气数据来自缓存降级数据，请注意核实" if degraded else None

        return {
            "final_response": AgentResponse(
                response_type="plans",
                content=explanation,
                plans=plans,
                data_time=data_time,
                defaults_applied=defaults_applied,
            )
        }

    explain_node.__name__ = "explain"
    return explain_node