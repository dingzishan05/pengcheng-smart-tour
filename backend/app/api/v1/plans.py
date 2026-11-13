from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request, Response

from app.agent.nodes.explain import make_explain_node
from app.agent.nodes.fetch_live import make_fetch_live_data_node
from app.agent.nodes.generate_candidates import make_generate_candidates_node
from app.agent.nodes.optimize import make_optimize_node
from app.agent.nodes.retrieve_pois import make_retrieve_pois_node
from app.agent.nodes.validate import make_validate_node
from app.agent.state import StructuredRequest, TourState
from app.api.deps import (
    get_session_factory,
    issue_session_token,
    resolve_conversation,
    set_session_cookie,
)
from app.core.config import settings
from app.core.errors import BizError, ErrorCode
from app.core.logging import conversation_id_var
from app.repositories.itinerary_repo import (
    ChangeEventRepository,
    ItineraryItemRepository,
    ItineraryRepository,
)
from app.services.itinerary_service import ItineraryService, to_plan_detail
from app.services.replan_service import ReplanChanges, ReplanService

router = APIRouter(tags=["plans"])

MIN_DURATION_HOURS = 2.0


async def _parse_request_payload(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        raise BizError(ErrorCode.PARAM_INVALID, "请求体必须是 JSON")
    if not isinstance(payload, dict):
        raise BizError(ErrorCode.PARAM_INVALID, "请求体必须是 JSON 对象")
    return payload


async def _load_structured_request(payload: dict) -> StructuredRequest:
    raw = payload.get("request")
    if not isinstance(raw, dict):
        raise BizError(ErrorCode.PARAM_INVALID, "request 字段缺失")
    try:
        structured = StructuredRequest.model_validate(raw)
    except Exception as exc:
        raise BizError(ErrorCode.PARAM_INVALID, "结构化需求校验失败", detail={"errors": str(exc)[:300]})
    if structured.duration_hours is not None and structured.duration_hours < MIN_DURATION_HOURS:
        raise BizError(ErrorCode.PARAM_INVALID, "游玩时长最短 2 小时")
    if structured.budget is not None and structured.budget < 0:
        raise BizError(ErrorCode.PARAM_INVALID, "预算不能为负数")
    return structured


def _infeasible_response(state: TourState) -> BizError:
    report = state.get("infeasibility")
    advice = {
        "min_budget_suggestion": None,
        "reasons": ["当前约束下无可行行程，请放宽预算/时长或更换日期"],
    }
    if report is not None:
        advice["reasons"] = [f"{r.detail}" for r in report.reasons] or advice["reasons"]
        if report.advice.min_budget_suggestion:
            advice["min_budget_suggestion"] = report.advice.min_budget_suggestion
        if report.advice.duration_suggestion:
            advice["reasons"].append(report.advice.duration_suggestion)
    return BizError(
        ErrorCode.NO_FEASIBLE_PLAN,
        "当前约束下无可行行程",
        detail={"advice": advice},
    )


@router.post("/plans")
async def create_plans(
    request: Request,
    response: Response,
    session_factory=Depends(get_session_factory),
):
    payload = await _parse_request_payload(request)
    structured = await _load_structured_request(payload)
    token = issue_session_token(request)
    conversation_id_str = payload.get("conversation_id")

    async with session_factory() as session:
        conversation = await resolve_conversation(
            session, token, conversation_id_str
        )
        await session.commit()
        conversation_id = conversation.id

    conversation_id_var.set(str(conversation_id))
    response.headers["X-Conversation-Id"] = str(conversation_id)

    state: TourState = {
        "conversation_id": str(conversation_id),
        "user_message": "",
        "intent": "itinerary_request",
        "structured_request": structured,
        "budget_deadline": time.monotonic() + settings.tour_total_budget_seconds,
    }
    if structured.visit_date is not None:
        state["visit_date"] = structured.visit_date.isoformat()

    retrieve = make_retrieve_pois_node(session_factory)
    fetch_live = make_fetch_live_data_node()
    generate = make_generate_candidates_node()
    optimize = make_optimize_node()
    validate = make_validate_node()
    explain = make_explain_node()

    state.update(await retrieve(state))
    if state.get("error") is not None or not state.get("candidate_pois"):
        raise _infeasible_response(state)
    state.update(await fetch_live(state))
    state.update(await generate(state))
    if state.get("error") is not None or not state.get("candidate_plans"):
        raise _infeasible_response(state)
    state.update(await optimize(state))
    if state.get("error") is not None or not state.get("plans"):
        raise _infeasible_response(state)
    state.update(await validate(state))
    state.update(await explain(state))

    final = state.get("final_response")
    plans = state.get("plans") or []
    if final is None or not plans:
        raise _infeasible_response(state)

    async with session_factory() as session:
        itin_service = ItineraryService(
            ItineraryRepository(session),
            ItineraryItemRepository(session),
            ChangeEventRepository(session),
        )
        snapshot = structured.model_dump(mode="json")
        saved = await itin_service.save_draft_plans(conversation_id, plans, snapshot)
        item_repo = ItineraryItemRepository(session)
        summaries = []
        details = []
        for itinerary in saved:
            items = await item_repo.list_by_itinerary(itinerary.id)
            detail = to_plan_detail(itinerary, items)
            if final is not None and final.content:
                detail["explanation"] = final.content
            details.append(detail)
            summaries.append(
                {
                    "plan_id": detail["plan_id"],
                    "plan_type": detail["plan_type"],
                    "total_cost": detail["total_cost"],
                    "total_minutes": detail["total_minutes"],
                    "transport_minutes": detail["transport_minutes"],
                    "highlights": detail["highlights"],
                    "suited_for": detail["suited_for"],
                }
            )
        await session.commit()

    set_session_cookie(response, token)
    return {
        "conversation_id": str(conversation_id),
        "plans": summaries,
        "plan_details": details,
        "data_time": final.data_time if final is not None else None,
    }


def _parse_plan_id(plan_id: str) -> int:
    try:
        return int(plan_id)
    except (TypeError, ValueError):
        raise BizError(ErrorCode.PARAM_INVALID, "plan_id 必须是数字")


@router.post("/plans/{plan_id}/refresh")
async def refresh_plan(
    plan_id: str,
    request: Request,
    response: Response,
    session_factory=Depends(get_session_factory),
):
    payload = await _parse_request_payload(request)
    trigger = payload.get("trigger")
    if trigger not in ("user_change", "data_change"):
        raise BizError(ErrorCode.PARAM_INVALID, "trigger 必须是 user_change 或 data_change")
    changes_raw = payload.get("changes") or {}
    if not isinstance(changes_raw, dict) or not changes_raw:
        raise BizError(ErrorCode.PARAM_INVALID, "changes 不能为空")
    replace = changes_raw.get("replace")
    try:
        changes = ReplanChanges(
            budget=float(changes_raw["budget"]) if changes_raw.get("budget") is not None else None,
            replace=replace if isinstance(replace, dict) else None,
            price_hike=changes_raw.get("price_hike") if isinstance(changes_raw.get("price_hike"), dict) else None,
            poi_closed=changes_raw.get("poi_closed") if isinstance(changes_raw.get("poi_closed"), dict) else None,
            heavy_rain=bool(changes_raw.get("heavy_rain", False)),
        )
    except (TypeError, ValueError):
        raise BizError(ErrorCode.PARAM_INVALID, "changes 内容非法")

    token = issue_session_token(request)
    conversation_id_str = payload.get("conversation_id")
    async with session_factory() as session:
        conversation = await resolve_conversation(
            session, token, conversation_id_str, create_when_missing=False
        )
        await session.commit()
        conversation_id = conversation.id

    service = ReplanService()
    numeric_id = _parse_plan_id(plan_id)
    outcome = await service.refresh(
        session_factory, conversation_id, numeric_id, trigger, changes
    )
    set_session_cookie(response, token)
    return outcome


@router.post("/plans/{plan_id}/confirm")
async def confirm_plan(
    plan_id: str,
    request: Request,
    response: Response,
    session_factory=Depends(get_session_factory),
):
    token = issue_session_token(request)
    conversation_id_str = request.query_params.get("conversation_id")
    async with session_factory() as session:
        conversation = await resolve_conversation(
            session, token, conversation_id_str, create_when_missing=False
        )
        conversation_id = conversation.id
        itin_service = ItineraryService(
            ItineraryRepository(session),
            ItineraryItemRepository(session),
            ChangeEventRepository(session),
        )
        numeric_id = _parse_plan_id(plan_id)
        confirmed = await itin_service.confirm_plan(conversation_id, numeric_id)
        items = await ItineraryItemRepository(session).list_by_itinerary(confirmed.id)
        detail = to_plan_detail(confirmed, items)
        await session.commit()

    set_session_cookie(response, token)
    return {
        "plan_id": detail["plan_id"],
        "status": "confirmed",
        "plan_detail": detail,
    }

@router.get("/plans/{plan_id}")
async def get_plan_detail(
    plan_id: str,
    request: Request,
    response: Response,
    session_factory=Depends(get_session_factory),
):
    token = issue_session_token(request)
    conversation_id_str = request.query_params.get("conversation_id")
    async with session_factory() as session:
        conversation = await resolve_conversation(
            session, token, conversation_id_str, create_when_missing=False
        )
        conversation_id = conversation.id
        itin_service = ItineraryService(
            ItineraryRepository(session),
            ItineraryItemRepository(session),
            ChangeEventRepository(session),
        )
        detail = await itin_service.get_detail(conversation_id, _parse_plan_id(plan_id))

    set_session_cookie(response, token)
    return detail