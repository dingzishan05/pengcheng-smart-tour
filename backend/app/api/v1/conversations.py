from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import (
    get_session_factory,
    issue_session_token,
    resolve_conversation,
    set_session_cookie,
)
from app.core.errors import BizError, ErrorCode
from app.repositories.conversation_repo import ConversationRepository, MessageRepository
from app.repositories.itinerary_repo import ItineraryRepository

router = APIRouter(tags=["conversations"])


@router.get("/conversations")
async def list_conversations(
    request: Request,
    response: Response,
    session_factory=Depends(get_session_factory),
):
    token = issue_session_token(request)
    async with session_factory() as session:
        conversations = await ConversationRepository(session).list_by_token(token)
        pending_counts = await ItineraryRepository(session).count_pending_by_conversations(
            [c.id for c in conversations]
        )
        items = [
            {
                "conversation_id": str(c.id),
                "title": c.title,
                "status": c.status,
                "last_active_at": c.last_active_at.isoformat() if c.last_active_at else None,
                "pending_suggestion_count": pending_counts.get(c.id, 0),
            }
            for c in conversations
        ]
    set_session_cookie(response, token)
    return {"items": items, "total": len(items)}


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    request: Request,
    response: Response,
    page: int = 1,
    page_size: int = 20,
    session_factory=Depends(get_session_factory),
):
    if page < 1 or page_size < 1 or page_size > 100:
        raise BizError(ErrorCode.PARAM_INVALID, "分页参数非法（page≥1，1≤page_size≤100）")
    token = issue_session_token(request)
    async with session_factory() as session:
        try:
            await resolve_conversation(
                session, token, conversation_id, create_when_missing=False
            )
        except BizError as exc:
            if exc.code == ErrorCode.PARAM_INVALID:
                raise BizError(ErrorCode.PARAM_INVALID, "conversation_id 非法")
            raise
        numeric_id = int(conversation_id)
        message_repo = MessageRepository(session)
        total = await message_repo.count(numeric_id)
        history = await message_repo.list_history(
            numeric_id, limit=page_size, offset=(page - 1) * page_size
        )
        items = [
            {
                "message_id": str(m.id),
                "role": m.role,
                "content": m.content,
                "response_type": m.response_type,
                "plan_refs": [str(p) for p in (m.plan_refs or [])],
                "sent_at": m.sent_at.isoformat() if m.sent_at else None,
            }
            for m in history
        ]
    set_session_cookie(response, token)
    return {"items": items, "total": total, "page": page, "page_size": page_size}

@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    request: Request,
    response: Response,
    session_factory=Depends(get_session_factory),
):
    token = issue_session_token(request)
    async with session_factory() as session:
        try:
            conversation = await resolve_conversation(
                session, token, conversation_id, create_when_missing=False
            )
        except BizError as exc:
            if exc.code == ErrorCode.PARAM_INVALID:
                raise BizError(ErrorCode.PARAM_INVALID, "conversation_id 非法")
            raise
        await ConversationRepository(session).soft_delete(conversation)
        await session.commit()

    set_session_cookie(response, token)
    return {"conversation_id": conversation_id, "status": "deleted"}