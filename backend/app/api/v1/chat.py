from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response

from app.agent import runtime
from app.agent.state import AgentError, AgentResponse, StructuredRequest
from app.api.deps import (
    get_session_factory,
    issue_session_token,
    resolve_conversation,
    set_session_cookie,
)
from app.core.config import settings
from app.core.errors import BizError, ErrorCode
from app.core.logging import conversation_id_var, get_logger
from app.repositories.conversation_repo import ConversationRepository, MessageRepository
from app.repositories.itinerary_repo import (
    ChangeEventRepository,
    ItineraryItemRepository,
    ItineraryRepository,
)
from app.services.itinerary_service import (
    ItineraryService,
    to_plan_summary,
)

logger = get_logger("app.api.chat")

router = APIRouter(tags=["chat"])


class ChatRequest:
    def __init__(self, conversation_id: str | None = None, message: str = "") -> None:
        self.conversation_id = conversation_id
        self.message = message


async def parse_chat_body(request: Request) -> ChatRequest:
    try:
        payload = await request.json()
    except Exception:
        raise BizError(ErrorCode.PARAM_INVALID, "请求体必须是 JSON")
    conversation_id = payload.get("conversation_id")
    message = payload.get("message", "")
    if not isinstance(message, str) or not message.strip():
        raise BizError(ErrorCode.PARAM_INVALID, "消息不能为空")
    if len(message) > settings.user_input_max_length:
        raise BizError(
            ErrorCode.PARAM_INVALID,
            f"消息长度不能超过 {settings.user_input_max_length} 字符",
        )
    if conversation_id is not None and not isinstance(conversation_id, str):
        raise BizError(ErrorCode.PARAM_INVALID, "conversation_id 必须是字符串")
    return ChatRequest(conversation_id=conversation_id, message=message)


@router.post("/chat")
async def chat(
    request: Request,
    response: Response,
    session_factory=Depends(get_session_factory),
):
    body = await parse_chat_body(request)
    token = issue_session_token(request)

    async with session_factory() as session:
        conversation = await resolve_conversation(session, token, body.conversation_id)
        message_repo = MessageRepository(session)
        await message_repo.append(conversation.id, "user", body.message)
        await session.commit()
        conversation_id = conversation.id

    conversation_id_var.set(str(conversation_id))
    response.headers["X-Conversation-Id"] = str(conversation_id)

    graph = await runtime.get_graph()
    try:
        result = await graph.ainvoke(
            {
                "conversation_id": str(conversation_id),
                "user_message": body.message,
            },
            config={"configurable": {"thread_id": str(conversation_id)}},
        )
    except Exception as exc:
        logger.error("graph invoke failed", extra={"kind": "algorithm", "error": str(exc)[:200]})
        result = {}

    final: AgentResponse = result.get("final_response") or AgentResponse(
        response_type="fallback",
        content="系统繁忙，请稍后重试。",
    )
    error: AgentError | None = result.get("error")

    plans_payload = None
    plan_refs: list[int] = []
    data_time = final.data_time

    async with session_factory() as session:
        itin_service = ItineraryService(
            ItineraryRepository(session),
            ItineraryItemRepository(session),
            ChangeEventRepository(session),
        )
        if final.response_type == "plans" and final.plans:
            snapshot_obj: StructuredRequest | None = result.get("structured_request")
            snapshot = (
                snapshot_obj.model_dump(mode="json")
                if snapshot_obj is not None
                else {"visit_date": result.get("visit_date")}
            )

            saved = await itin_service.save_draft_plans(
                conversation_id,
                final.plans,
                snapshot,
                data_time=datetime.now(timezone.utc) if data_time else None,
            )
            plan_refs = [i.id for i in saved]
            plans_payload = []
            for itinerary in saved:
                items = await ItineraryItemRepository(session).list_by_itinerary(
                    itinerary.id
                )
                plans_payload.append(to_plan_summary(itinerary, items))
        message_repo = MessageRepository(session)
        await message_repo.append(
            conversation.id,
            "system",
            final.content,
            final.response_type,
            plan_refs or None,
        )
        conv_repo = ConversationRepository(session)
        conversation = await conv_repo.get(conversation_id)
        if conversation is not None:
            await conv_repo.touch(
                conversation, title=body.message[:50] if body.message else None
            )
        await session.commit()

    set_session_cookie(response, token)

    payload: dict = {
        "conversation_id": str(conversation_id),
        "response_type": final.response_type,
        "content": final.content,
        "plans": plans_payload,
        "plan_details_available": plans_payload is not None,
        "sources": [s.model_dump() for s in final.sources] if final.sources else None,
        "data_time": data_time,
        "code": None,
        "message": None,
    }
    if error is not None and error.code == "50003":
        payload["code"] = 50003
        payload["message"] = "AI 服务暂时不可用，请稍后重试（您刚才的输入已保存，可直接重发）"
    return payload