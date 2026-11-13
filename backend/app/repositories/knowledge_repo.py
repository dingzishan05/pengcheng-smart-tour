from sqlalchemy import select

from app.models import AuditLog, DataSource, KnowledgeChunk, KnowledgeDocument
from app.repositories.base import BaseRepository


class KnowledgeRepository(BaseRepository[KnowledgeDocument]):
    def __init__(self, session) -> None:
        super().__init__(session, KnowledgeDocument)

    async def get_by_title(self, title: str) -> KnowledgeDocument | None:
        stmt = select(KnowledgeDocument).where(KnowledgeDocument.title == title)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def latest_version(self, title: str) -> int:
        stmt = select(KnowledgeDocument.version).where(KnowledgeDocument.title == title)
        versions = list((await self.session.execute(stmt)).scalars().all())
        return max(versions) if versions else 0

    async def list_documents(
        self, topic: str | None = None, status: str | None = None
    ) -> list[KnowledgeDocument]:
        stmt = select(KnowledgeDocument).order_by(KnowledgeDocument.id.desc())
        if topic is not None:
            stmt = stmt.where(KnowledgeDocument.topic == topic)
        if status is not None:
            stmt = stmt.where(KnowledgeDocument.status == status)
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_versions(self, title: str) -> list[KnowledgeDocument]:
        stmt = (
            select(KnowledgeDocument)
            .where(KnowledgeDocument.title == title)
            .order_by(KnowledgeDocument.version.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())


class KnowledgeChunkRepository(BaseRepository[KnowledgeChunk]):
    def __init__(self, session) -> None:
        super().__init__(session, KnowledgeChunk)

    async def search_similar(
        self, query_embedding: list[float], top_k: int = 8, topic: str | None = None
    ) -> list[tuple[KnowledgeChunk, float]]:
        distance = KnowledgeChunk.embedding.cosine_distance(query_embedding)
        stmt = (
            select(KnowledgeChunk, distance.label("distance"))
            .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .where(KnowledgeDocument.status == "有效")
            .order_by(distance.asc())
            .limit(top_k)
        )
        if topic is not None:
            stmt = stmt.where(KnowledgeDocument.topic == topic)
        rows = (await self.session.execute(stmt)).all()
        return [(chunk, 1.0 - float(dist)) for chunk, dist in rows]

    async def bulk_add(self, chunks: list[KnowledgeChunk]) -> None:
        self.session.add_all(chunks)
        await self.session.flush()

    async def search_by_keywords(
        self, keywords: list[str], top_k: int = 8, topic: str | None = None
    ) -> list[KnowledgeChunk]:
        if not keywords:
            return []
        from sqlalchemy import or_

        conditions = [KnowledgeChunk.chunk_text.ilike(f"%{kw}%") for kw in keywords]
        stmt = (
            select(KnowledgeChunk)
            .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .where(KnowledgeDocument.status == "有效", or_(*conditions))
            .limit(top_k)
        )
        if topic is not None:
            stmt = stmt.where(KnowledgeDocument.topic == topic)
        return list((await self.session.execute(stmt)).scalars().all())

    async def delete_by_document(self, document_id: int) -> None:
        stmt = select(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id)
        chunks = list((await self.session.execute(stmt)).scalars().all())
        for chunk in chunks:
            await self.session.delete(chunk)
        await self.session.flush()


class DataSourceRepository(BaseRepository[DataSource]):
    def __init__(self, session) -> None:
        super().__init__(session, DataSource)

    async def get_by_name(self, name: str) -> DataSource | None:
        stmt = select(DataSource).where(DataSource.name == name)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_all(self) -> list[DataSource]:
        return list((await self.session.execute(select(DataSource))).scalars().all())


class AuditLogRepository(BaseRepository[AuditLog]):
    def __init__(self, session) -> None:
        super().__init__(session, AuditLog)

    async def append(
        self,
        operator_id: int,
        action: str,
        entity_type: str,
        entity_id: int,
        before_value: dict | None = None,
        after_value: dict | None = None,
    ) -> AuditLog:
        log = AuditLog(
            operator_id=operator_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before_value=before_value,
            after_value=after_value,
        )
        return await self.add(log)
    async def list_filtered(
        self,
        operator_id: int | None = None,
        entity_type: str | None = None,
        entity_id: int | None = None,
        since=None,
        until=None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AuditLog], int]:
        from sqlalchemy import func

        conditions = []
        if operator_id is not None:
            conditions.append(AuditLog.operator_id == operator_id)
        if entity_type is not None:
            conditions.append(AuditLog.entity_type == entity_type)
        if entity_id is not None:
            conditions.append(AuditLog.entity_id == entity_id)
        if since is not None:
            conditions.append(AuditLog.operated_at >= since)
        if until is not None:
            conditions.append(AuditLog.operated_at <= until)
        base = select(AuditLog)
        if conditions:
            base = base.where(*conditions)
        total = (
            await self.session.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        rows = (
            await self.session.execute(
                base.order_by(AuditLog.operated_at.desc(), AuditLog.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
        return list(rows), int(total)