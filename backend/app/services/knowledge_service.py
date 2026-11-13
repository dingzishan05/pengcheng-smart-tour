from __future__ import annotations

from app.core.errors import BizError, ErrorCode
from app.repositories.knowledge_repo import (
    KnowledgeChunkRepository,
    KnowledgeRepository,
)
from app.services.audit_service import AuditService


class KnowledgeService:
    def __init__(
        self,
        knowledge_repo: KnowledgeRepository,
        chunk_repo: KnowledgeChunkRepository,
        audit_service: AuditService,
    ) -> None:
        self.knowledge_repo = knowledge_repo
        self.chunk_repo = chunk_repo
        self.audit_service = audit_service

    async def ingest(
        self,
        session,
        *,
        title: str,
        topic: str,
        content: str,
        source: str,
        operator_id: int,
    ) -> dict:
        from app.rag.ingestion import ingest_document

        document = await ingest_document(
            session,
            title=title,
            topic=topic,
            content=content,
            source=source,
            operator_id=operator_id,
        )
        return {
            "status": "完成",
            "document_id": document.id,
            "title": document.title,
            "version": document.version,
        }

    async def set_status(
        self, *, document_id: int, status: str, operator_id: int
    ) -> dict:
        if status not in ("有效", "失效"):
            raise BizError(ErrorCode.PARAM_INVALID, "status 必须是 有效 或 失效")
        document = await self.knowledge_repo.get(document_id)
        if document is None:
            raise BizError(ErrorCode.NOT_FOUND, "知识文档不存在")
        before = {"status": document.status}
        document.status = status
        await self.audit_service.log(
            operator_id=operator_id,
            action="knowledge_status_change",
            entity_type="knowledge_document",
            entity_id=document.id,
            before_value=before,
            after_value={"status": status},
        )
        return {
            "document_id": document.id,
            "title": document.title,
            "version": document.version,
            "status": document.status,
        }

    async def list_versions(self, title: str) -> list[dict]:
        docs = await self.knowledge_repo.list_versions(title)
        return [
            {
                "document_id": d.id,
                "title": d.title,
                "topic": d.topic,
                "version": d.version,
                "status": d.status,
                "ingested_at": d.ingested_at.isoformat() if d.ingested_at else None,
            }
            for d in docs
        ]

    async def list_documents(
        self, topic: str | None = None, status: str | None = None
    ) -> list[dict]:
        docs = await self.knowledge_repo.list_documents(topic=topic, status=status)
        return [
            {
                "document_id": d.id,
                "title": d.title,
                "topic": d.topic,
                "version": d.version,
                "status": d.status,
                "ingested_at": d.ingested_at.isoformat() if d.ingested_at else None,
            }
            for d in docs
        ]