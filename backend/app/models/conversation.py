from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BaseModel


class Conversation(BaseModel):
    __tablename__ = "conversations"

    session_token: Mapped[UUID] = mapped_column(nullable=False, unique=True)
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Message(BaseModel):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user','system')", name="ck_message_role"),
        Index("idx_messages_conversation_time", "conversation_id", "sent_at"),
    )

    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    response_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    plan_refs: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.now)