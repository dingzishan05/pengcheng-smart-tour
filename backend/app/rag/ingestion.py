from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.service import invalidate_embedding_caches
from app.clients.llm import embed_texts
from app.core.logging import get_logger
from app.models import KnowledgeChunk, KnowledgeDocument
from app.repositories.knowledge_repo import (
    AuditLogRepository,
    KnowledgeChunkRepository,
    KnowledgeRepository,
)

logger = get_logger("app.rag.ingestion")

TOPICS = ("两汉文化", "景点介绍", "特色美食", "交通常识")

CHUNK_MAX_CHARS = 600
CHUNK_OVERLAP_CHARS = 80


class IngestionError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class ChunkDraft:
    chunk_seq: int
    chunk_text: str
    is_title: bool
    metadata: dict


def validate_document(title: str, topic: str, content: str, source: str) -> None:
    if not title or not title.strip():
        raise IngestionError("标题不能为空")
    if not source or not source.strip():
        raise IngestionError("来源必填：无出处内容禁止入库")
    if topic not in TOPICS:
        raise IngestionError(f"主题必须是 {'/'.join(TOPICS)} 之一")
    if not content or len(content.strip()) < 20:
        raise IngestionError("正文不能为空且至少 20 字")


_FULLWIDTH_MAP = {
    ",": "，", ";": "；", ":": "：", "?": "？", "!": "！", "(": "（", ")": "）",
}


def clean_content(content: str) -> str:
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    for half, full in _FULLWIDTH_MAP.items():
        text = text.replace(half, full)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [line.strip() for line in text.split("\n")]
    seen: set[str] = set()
    deduped: list[str] = []
    for line in lines:
        if not line:
            if deduped and deduped[-1] != "":
                deduped.append("")
            continue
        if line in seen:
            continue
        seen.add(line)
        deduped.append(line)
    return "\n".join(deduped).strip()


def _merge_paragraphs(paragraphs: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        if current and current_len + len(para) > CHUNK_MAX_CHARS:
            blocks.append(current)
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += len(para)
    if current:
        blocks.append(current)
    return blocks


def chunk_content(title: str, content: str, doc_meta: dict) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = [ChunkDraft(0, title, True, {**doc_meta, "section": "标题"})]
    paragraphs = [p.strip() for p in content.split("\n") if p.strip()]
    if not paragraphs:
        return drafts
    blocks = _merge_paragraphs(paragraphs)
    seq = 1
    for block in blocks:
        text = "\n".join(block)
        drafts.append(ChunkDraft(seq, text, False, {**doc_meta, "section": f"第{seq}段"}))
        seq += 1
    for draft in drafts[2:]:
        previous = drafts[drafts.index(draft) - 1]
        if previous.is_title:
            continue
        draft.chunk_text = previous.chunk_text[-CHUNK_OVERLAP_CHARS:] + "\n" + draft.chunk_text
    return drafts


async def ingest_document(
    session: AsyncSession,
    *,
    title: str,
    topic: str,
    content: str,
    source: str,
    operator_id: int,

) -> KnowledgeDocument:
    validate_document(title, topic, content, source)
    doc_repo = KnowledgeRepository(session)
    chunk_repo = KnowledgeChunkRepository(session)
    audit_repo = AuditLogRepository(session)

    latest = await doc_repo.latest_version(title)
    version = latest + 1

    cleaned = clean_content(content)
    meta = {"doc_title": title, "topic": topic, "source": source, "version": version}
    drafts = chunk_content(title, cleaned, meta)

    embeddings = await embed_texts([d.chunk_text for d in drafts])

    previous_docs = await doc_repo.list_versions(title)
    for old in previous_docs:
        if old.status == "有效":
            old.status = "失效"
            await chunk_repo.delete_by_document(old.id)

    document = KnowledgeDocument(
        title=title,
        topic=topic,
        content=cleaned,
        source=source,
        status="有效",
        version=version,
    )
    document = await doc_repo.add(document)

    for draft, embedding in zip(drafts, embeddings):
        session.add(
            KnowledgeChunk(
                document_id=document.id,
                chunk_seq=draft.chunk_seq,
                chunk_text=draft.chunk_text,
                embedding=embedding,
                metadata_=draft.metadata,
            )
        )
    await session.flush()

    await audit_repo.append(
        operator_id=operator_id,
        action="ingest",
        entity_type="knowledge_document",
        entity_id=document.id,
        after_value={"title": title, "topic": topic, "version": version},
    )

    await invalidate_embedding_caches()
    logger.info(
        "knowledge document ingested",
        extra={"kind": "data", "title": title, "version": version, "chunks": len(drafts)},
    )
    return document