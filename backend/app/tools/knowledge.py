from __future__ import annotations

from pydantic import BaseModel, Field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.rag.retriever import RetrievalGate, retrieve
from app.tools.base import ToolErrorKind, ToolMeta, ToolResult

logger = get_logger("app.tools.knowledge")


class KnowledgeQuery(BaseModel):
    question: str
    topic: str | None = None
    top_k: int = 5


class RetrievedChunkInfo(BaseModel):
    chunk_id: int
    doc_title: str
    topic: str
    source: str
    chunk_text: str
    similarity: float
    doc_version: int | None = None


class KnowledgeRetrievalOutput(BaseModel):
    chunks: list[RetrievedChunkInfo] = Field(default_factory=list)
    low_confidence: bool = False
    insufficient: bool = False
    degraded: bool = False
    expanded_query: str = ""


async def retrieve_xuzhou_knowledge(
    session: AsyncSession, query: KnowledgeQuery
) -> ToolResult[KnowledgeRetrievalOutput]:
    try:
        result = await retrieve(session, query.question, topic=query.topic, top_k=8)
        chunks = [
            RetrievedChunkInfo(
                chunk_id=chunk.chunk_id,
                doc_title=chunk.metadata.get("doc_title", ""),
                topic=chunk.metadata.get("topic", ""),
                source=chunk.metadata.get("source", ""),
                chunk_text=chunk.chunk_text,
                similarity=chunk.similarity,
                doc_version=chunk.metadata.get("version"),
            )
            for chunk in result.chunks
        ]
        output = KnowledgeRetrievalOutput(
            chunks=chunks,
            low_confidence=result.low_confidence,
            insufficient=result.gate == RetrievalGate.INSUFFICIENT,
            degraded=result.degraded,
            expanded_query=result.expanded_query,
        )
        meta = ToolMeta(source_channel="pgvector" if not result.degraded else "keyword_fallback", degraded=result.degraded)
        return ToolResult.success(output, meta)
    except Exception as exc:
        logger.error("retrieve_xuzhou_knowledge failed", extra={"kind": "external", "error": str(exc)[:200]})
        return ToolResult.failure(ToolErrorKind.EXTERNAL, "50002", f"知识检索失败: {exc.__class__.__name__}")