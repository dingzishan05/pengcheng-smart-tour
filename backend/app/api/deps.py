from __future__ import annotations

from uuid import uuid4

from fastapi import Request, Response

from app.core.db import SessionFactory
from app.core.errors import BizError, ErrorCode
from app.models import Conversation
from app.repositories.conversation_repo import ConversationRepository

SESSION_COOKIE = "pczy_session"
SESSION_MAX_AGE = 30 * 24 * 3600


def get_session_factory():
    return SessionFactory


def issue_session_token(request: Request) -> str:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        return token
    return str(uuid4())


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


async def resolve_conversation(
    session,
    token: str,
    conversation_id: str | None,
    *,
    create_when_missing: bool = True,
) -> Conversation:
    repo = ConversationRepository(session)
    if conversation_id:
        try:
            conv_id = int(conversation_id)
        except (TypeError, ValueError):
            raise BizError(ErrorCode.PARAM_INVALID, "conversation_id 非法")
        conversation = await repo.get(conv_id)
        if conversation is None or str(conversation.session_token) != token:
            raise BizError(
                ErrorCode.NOT_FOUND,
                "会话不存在或不属于当前访问者",
                session_id=conversation_id,
            )
        await repo.touch(conversation)
        return conversation
    conversations = await repo.list_by_token(token)
    if conversations:
        await repo.touch(conversations[0])
        return conversations[0]
    if not create_when_missing:
        raise BizError(ErrorCode.SESSION_INVALID, "会话不存在")
    return await repo.create(token)