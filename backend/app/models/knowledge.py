from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from app.models.base import BaseModel


class KnowledgeDocument(BaseModel):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        CheckConstraint(
            "topic IN ('两汉文化','景点介绍','特色美食','交通常识')", name="ck_kdoc_topic"
        ),
        CheckConstraint("length(content) > 0", name="ck_kdoc_content"),
        CheckConstraint("status IN ('有效','失效')", name="ck_kdoc_status"),
        UniqueConstraint("title", "version", name="uq_kdoc_title_version"),
    )

    topic: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="有效")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.now
    )


class KnowledgeChunk(BaseModel):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_seq", name="uq_chunk_seq"),
        Index("idx_chunks_document", "document_id"),
    )

    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("knowledge_documents.id"), nullable=False
    )
    chunk_seq: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list] = mapped_column(Vector(1024), nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False)