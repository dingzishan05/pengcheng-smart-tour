from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.keys import embedding_key
from app.cache.service import cache_get, cache_set
from app.clients.llm import EmbeddingUnavailableError, embed_texts
from app.core.config import settings
from app.core.logging import get_logger
from app.repositories.knowledge_repo import KnowledgeChunkRepository
from app.models import KnowledgeChunk

logger = get_logger("app.rag.retriever")

QUERY_EMBEDDING_TTL = 24 * 3600
TOP_K = 8

STOP_WORDS = {
    "的", "了", "吗", "呢", "吧", "在", "是", "我", "有", "和", "就", "不", "都",
    "怎么", "如何", "请问", "一下", "什么", "哪些", "哪里", "有没有", "告诉",
}

SYNONYM_EXPANSIONS: dict[str, list[str]] = {
    "汉墓": ["龟山汉墓", "狮子山楚王陵"],
    "兵马俑": ["汉兵马俑"],
    "汉画像石": ["汉画像石艺术馆"],
    "楚王陵": ["狮子山楚王陵"],
    "地锅": ["地锅鸡"],
    "地铁": ["徐州地铁"],
    "高铁": ["徐州东站"],
}


class RetrievalGate(str, Enum):
    HIT = "hit"
    WEAK = "weak"
    INSUFFICIENT = "insufficient"


@dataclass
class RetrievedChunk:
    chunk_id: int
    chunk_text: str
    similarity: float
    metadata: dict


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk] = field(default_factory=list)
    gate: RetrievalGate = RetrievalGate.INSUFFICIENT
    max_similarity: float = 0.0
    low_confidence: bool = False
    degraded: bool = False
    expanded_query: str = ""


def preprocess_query(query: str) -> str:
    cleaned = re.sub(r"\s+", " ", query.strip())
    for word in STOP_WORDS:
        cleaned = cleaned.replace(word, " ")
    tokens = [t for t in re.split(r"[ ，。？！,.?!]", cleaned) if t]
    if not tokens:
        tokens = [t for t in re.split(r"[ ，。？！,.?!]", query.strip()) if t] or [query.strip()]
    expansions: list[str] = []
    for token in tokens:
        for key, values in SYNONYM_EXPANSIONS.items():
            if key in token and not any(v in token for v in values):
                expansions.extend(v for v in values if v not in expansions)
    return " ".join(tokens + expansions)


def query_hash(query: str) -> str:
    return hashlib.md5(query.encode("utf-8")).hexdigest()


def evaluate_gate(max_similarity: float) -> RetrievalGate:
    if max_similarity >= settings.rag_similarity_threshold:
        return RetrievalGate.HIT
    if max_similarity >= settings.rag_weak_threshold:
        return RetrievalGate.WEAK
    return RetrievalGate.INSUFFICIENT


async def get_query_embedding(query: str) -> list[float]:
    key = embedding_key(query_hash(query))
    cached = await cache_get(key)
    if cached is not None:
        return cached
    embedding = (await embed_texts([query]))[0]
    await cache_set(key, embedding, QUERY_EMBEDDING_TTL)
    return embedding


def _to_retrieved(chunk: KnowledgeChunk, similarity: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk.id,
        chunk_text=chunk.chunk_text,
        similarity=round(similarity, 4),
        metadata=dict(chunk.metadata_ or {}),
    )


async def retrieve(
    session: AsyncSession,
    query: str,
    topic: str | None = None,
    top_k: int = TOP_K,
) -> RetrievalResult:
    expanded = preprocess_query(query)
    repo = KnowledgeChunkRepository(session)

    for attempt in (1, 2):
        try:
            embedding = await get_query_embedding(expanded)
            rows = await repo.search_similar(embedding, top_k=top_k, topic=topic)
            result = RetrievalResult(expanded_query=expanded)
            result.chunks = [_to_retrieved(chunk, sim) for chunk, sim in rows]
            result.max_similarity = result.chunks[0].similarity if result.chunks else 0.0
            result.gate = evaluate_gate(result.max_similarity)
            if result.gate == RetrievalGate.HIT:
                result.chunks = result.chunks[:5]
            elif result.gate == RetrievalGate.WEAK:
                result.chunks = result.chunks[:3]
                result.low_confidence = True
            else:
                result.chunks = []
            return result
        except EmbeddingUnavailableError:
            if attempt == 1:
                logger.warning(
                    "embedding service failed, retrying",
                    extra={"kind": "external", "query": query[:50]},
                )
                continue
            break

    logger.warning(
        "embedding unavailable, falling back to keyword search",
        extra={"kind": "external", "query": query[:50]},
    )
    keywords = [t for t in expanded.split() if t]
    rows = await repo.search_by_keywords(keywords, top_k=top_k, topic=topic)
    result = RetrievalResult(
        chunks=[_to_retrieved(chunk, 0.0) for chunk in rows],
        gate=RetrievalGate.WEAK if rows else RetrievalGate.INSUFFICIENT,
        low_confidence=True,
        degraded=True,
        expanded_query=expanded,
    )
    result.max_similarity = 0.0
    return result