from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.nodes.clarify import make_ask_clarification_node
from app.agent.nodes.explain import make_explain_node
from app.agent.nodes.fetch_live import make_fetch_live_data_node
from app.agent.nodes.generate_candidates import make_generate_candidates_node
from app.agent.nodes.optimize import make_optimize_node
from app.agent.nodes.parse_request import make_parse_request_node
from app.agent.nodes.rag_qa import make_rag_qa_node
from app.agent.nodes.retrieve_pois import make_retrieve_pois_node
from app.agent.nodes.validate import make_validate_node
from app.agent.state import REQUIRED_FIELDS, TourState

CLARIFY_ROUND_LIMIT = 2


def route_after_parse(state: TourState) -> str:
    """R1—R5 条件路由（按 design/02 §5 真值表顺序评估）。"""
    if state.get("error") is not None:
        return END
    intent = state.get("intent")
    if intent == "knowledge_qa":
        return "rag_qa"
    if intent == "invalid_input":
        return END
    if intent == "itinerary_adjust":
        return END
    missing = set(state.get("missing_fields") or [])
    round_ = state.get("clarify_round", 0)
    if (
        missing & set(REQUIRED_FIELDS)
        and round_ < CLARIFY_ROUND_LIMIT
        and not state.get("no_more_questions")
    ):
        return "ask_clarification"
    return "retrieve_pois"


def route_after_generate(state: TourState) -> str:
    """R6：候选为空 → END（infeasibility 输出）。"""
    if state.get("error") is not None:
        return END
    if not state.get("candidate_plans"):
        return END
    return "optimize"


def route_after_rag(state: TourState) -> str:
    """R7/R8 之外的行程衔接分支（spec 5.6.1.5）。"""
    if state.get("error") is not None:
        return END
    if state.get("travel_intent_poi"):
        missing = set(state.get("missing_fields") or [])
        round_ = state.get("clarify_round", 0)
        if (
            missing & set(REQUIRED_FIELDS)
            and round_ < CLARIFY_ROUND_LIMIT
            and not state.get("no_more_questions")
        ):
            return "ask_clarification"
        return "retrieve_pois"
    return END


def build_graph(
    session_factory,
    checkpointer=None,
    parse_fn=None,
    explain_fn=None,
    answer_fn=None,
    clock=None,
):
    graph = StateGraph(TourState)

    graph.add_node("parse_request", make_parse_request_node(parse_fn=parse_fn, clock=clock))
    graph.add_node("ask_clarification", make_ask_clarification_node())
    graph.add_node("rag_qa", make_rag_qa_node(session_factory, answer_fn=answer_fn))
    graph.add_node("retrieve_pois", make_retrieve_pois_node(session_factory))
    graph.add_node("fetch_live_data", make_fetch_live_data_node())
    graph.add_node("generate_candidates", make_generate_candidates_node())
    graph.add_node("optimize", make_optimize_node(clock=clock))
    graph.add_node("validate", make_validate_node())
    graph.add_node("explain", make_explain_node(explain_fn=explain_fn, clock=clock))

    graph.add_edge(START, "parse_request")
    graph.add_conditional_edges(
        "parse_request",
        route_after_parse,
        {
            "rag_qa": "rag_qa",
            "ask_clarification": "ask_clarification",
            "retrieve_pois": "retrieve_pois",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "rag_qa",
        route_after_rag,
        {
            "retrieve_pois": "retrieve_pois",
            "ask_clarification": "ask_clarification",
            END: END,
        },
    )
    graph.add_edge("ask_clarification", END)
    graph.add_edge("retrieve_pois", "fetch_live_data")
    graph.add_edge("fetch_live_data", "generate_candidates")
    graph.add_conditional_edges(
        "generate_candidates",
        route_after_generate,
        {"optimize": "optimize", END: END},
    )
    graph.add_edge("optimize", "validate")
    graph.add_edge("validate", "explain")
    graph.add_edge("explain", END)

    return graph.compile(checkpointer=checkpointer)