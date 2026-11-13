from __future__ import annotations

from app.agent.state import AgentResponse, MessageRecord, TourState

CLARIFY_QUESTIONS = {
    "duration": "您计划游玩多长时间（如一天、半天、4小时）",
    "budget": "您的预算大概是多少",
    "interest": "您对什么主题感兴趣（如汉文化、美食、自然风光）",
    "location": "您从哪里出发（如徐州东站、市中心）",
    "companions": "有同伴或特殊需求吗（如带老人孩子）",
}

MAX_QUESTIONS = 3


def build_clarify_question(missing_fields: list[str]) -> str:
    required = [f for f in ("duration", "budget") if f in missing_fields]
    suggested = [f for f in ("interest", "location", "companions") if f in missing_fields]
    ordered = required + suggested
    questions = [CLARIFY_QUESTIONS[f] for f in ordered[:MAX_QUESTIONS]]
    if not questions:
        return ""
    return "为了给您规划更合适的行程，想先确认几个信息：" + "？".join(questions) + "？"


def make_ask_clarification_node():
    async def ask_clarification_node(state: TourState) -> dict:
        missing = list(state.get("missing_fields") or [])
        round_ = state.get("clarify_round", 0)
        question = build_clarify_question(missing)
        new_round = round_ + 1
        messages = list(state.get("messages") or [])
        messages.append(MessageRecord(role="system", content=question, response_type="clarify"))
        return {
            "clarify_round": new_round,
            "messages": messages,
            "final_response": AgentResponse(response_type="clarify", content=question),
        }

    ask_clarification_node.__name__ = "ask_clarification"
    return ask_clarification_node