from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select

from app.models import Conversation, Itinerary, ItineraryItem, Message
from app.repositories.base import BaseRepository


class ConversationRepository(BaseRepository[Conversation]):
    def __init__(self, session) -> None:
        super().__init__(session, Conversation)

    async def get_by_session_token(self, session_token: str) -> Conversation | None:
        stmt = select(Conversation).where(
            Conversation.session_token == UUID(session_token),
            Conversation.status == "active",
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(self, session_token: str, title: str | None = None) -> Conversation:
        conversation = Conversation(
            session_token=UUID(session_token),
            title=title,
            status="active",
            last_active_at=datetime.now(timezone.utc),
        )
        return await self.add(conversation)

    async def list_by_token(self, session_token: str) -> list[Conversation]:
        stmt = (
            select(Conversation)
            .where(
                Conversation.session_token == UUID(session_token),
                Conversation.status == "active",
            )
            .order_by(Conversation.last_active_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def touch(self, conversation: Conversation, title: str | None = None) -> None:
        conversation.last_active_at = datetime.now(timezone.utc)
        if title is not None and conversation.title is None:
            conversation.title = title
        await self.session.flush()

    async def soft_delete(self, conversation: Conversation) -> None:
        conversation.status = "deleted"
        conversation.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def list_deleted_before(self, cutoff: datetime) -> list[Conversation]:
        stmt = select(Conversation).where(Conversation.deleted_at.isnot(None), Conversation.deleted_at < cutoff)
        return list((await self.session.execute(stmt)).scalars().all())

    async def purge_deleted_before(self, cutoff: datetime) -> int:
        from sqlalchemy import delete

        conversations = await self.list_deleted_before(cutoff)
        if not conversations:
            return 0
        conversation_ids = [c.id for c in conversations]
        await self.session.execute(
            delete(Message).where(Message.conversation_id.in_(conversation_ids))
        )
        await self.session.execute(
            delete(ItineraryItem).where(
                ItineraryItem.itinerary_id.in_(
                    select(Itinerary.id).where(Itinerary.conversation_id.in_(conversation_ids))
                )
            )
        )
        await self.session.execute(
            delete(Itinerary).where(Itinerary.conversation_id.in_(conversation_ids))
        )
        await self.session.execute(
            delete(Conversation).where(Conversation.id.in_(conversation_ids))
        )
        return len(conversation_ids)


class MessageRepository(BaseRepository[Message]):
    def __init__(self, session) -> None:
        super().__init__(session, Message)

    async def append(
        self,
        conversation_id: int,
        role: str,
        content: str,
        response_type: str | None = None,
        plan_refs: list[int] | None = None,
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            response_type=response_type,
            plan_refs=plan_refs,
            sent_at=datetime.now(timezone.utc),
        )
        return await self.add(message)

    async def list_history(
        self, conversation_id: int, limit: int = 50, offset: int = 0
    ) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sent_at.asc(), Message.id.asc())
            .offset(offset)
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def count(self, conversation_id: int) -> int:
        stmt = (
            select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
        )
        return (await self.session.execute(stmt)).scalar_one()