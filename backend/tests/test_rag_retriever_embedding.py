from __future__ import annotations

from types import SimpleNamespace

from app.clients.llm import EmbeddingUnavailableError
from app.core.config import settings
from app.rag.retriever import (
    QUERY_EMBEDDING_TTL,
    RetrievalGate,
    get_query_embedding,
    retrieve,
)


async def test_get_query_embedding_awaits_before_index(monkeypatch):
    vector = [0.1, 0.2, 0.3]
    embed_calls: list[list[str]] = []
    set_calls: list[tuple[str, list[float], int]] = []

    async def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        embed_calls.append(texts)
        return [vector]

    async def fake_cache_get(key: str):
        return None

    async def fake_cache_set(key: str, value, ttl: int):
        set_calls.append((key, value, ttl))

    monkeypatch.setattr("app.rag.retriever.embed_texts", fake_embed_texts)
    monkeypatch.setattr("app.rag.retriever.cache_get", fake_cache_get)
    monkeypatch.setattr("app.rag.retriever.cache_set", fake_cache_set)

    result = await get_query_embedding("云龙湖好玩吗")

    assert result == vector
    assert embed_calls == [["云龙湖好玩吗"]]
    assert len(set_calls) == 1
    assert set_calls[0][1] == vector
    assert set_calls[0][2] == QUERY_EMBEDDING_TTL


async def test_get_query_embedding_cache_hit_skips_embed(monkeypatch):
    vector = [0.9, 0.8, 0.7]

    async def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        raise AssertionError("cache hit must not call embed_texts")

    async def fake_cache_get(key: str):
        return vector

    async def fake_cache_set(key: str, value, ttl: int):
        raise AssertionError("cache hit must not write cache")

    monkeypatch.setattr("app.rag.retriever.embed_texts", fake_embed_texts)
    monkeypatch.setattr("app.rag.retriever.cache_get", fake_cache_get)
    monkeypatch.setattr("app.rag.retriever.cache_set", fake_cache_set)

    result = await get_query_embedding("龟山汉墓怎么走")

    assert result == vector


async def test_retrieve_real_coroutine_path_returns_hit(monkeypatch):
    vector = [0.5, 0.4, 0.3, 0.2]

    async def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        return [vector]

    async def fake_cache_get(key: str):
        return None

    async def fake_cache_set(key: str, value, ttl: int):
        pass

    class FakeRepo:
        def __init__(self, session):
            self.session = session

        async def search_similar(self, query_embedding, top_k=8, topic=None):
            chunk = SimpleNamespace(
                id=7,
                chunk_text="云龙湖是徐州的开放式湖泊景区",
                metadata_={"doc_title": "云龙湖景区指南"},
            )
            return [(chunk, settings.rag_similarity_threshold + 0.1)]

        async def search_by_keywords(self, keywords, top_k=8, topic=None):
            raise AssertionError("vector path must not fall back to keywords")

    monkeypatch.setattr("app.rag.retriever.embed_texts", fake_embed_texts)
    monkeypatch.setattr("app.rag.retriever.cache_get", fake_cache_get)
    monkeypatch.setattr("app.rag.retriever.cache_set", fake_cache_set)
    monkeypatch.setattr("app.rag.retriever.KnowledgeChunkRepository", FakeRepo)

    result = await retrieve(None, "云龙湖有什么好玩的")

    assert result.gate == RetrievalGate.HIT
    assert result.chunks
    assert result.chunks[0].chunk_id == 7
    assert result.chunks[0].similarity >= settings.rag_similarity_threshold
    assert result.degraded is False
    assert result.low_confidence is False


async def test_retrieve_embedding_unavailable_degrades(monkeypatch):
    async def fake_embed_texts(texts: list[str]) -> list[list[float]]:
        raise EmbeddingUnavailableError("向量服务不可用")

    async def fake_cache_get(key: str):
        return None

    async def fake_cache_set(key: str, value, ttl: int):
        pass

    class FakeRepo:
        def __init__(self, session):
            self.session = session

        async def search_similar(self, query_embedding, top_k=8, topic=None):
            raise AssertionError("embedding unavailable must not run vector search")

        async def search_by_keywords(self, keywords, top_k=8, topic=None):
            return [
                SimpleNamespace(
                    id=9,
                    chunk_text="云湖区关键词命中的降级结果",
                    metadata_={"doc_title": "降级数据"},
                )
            ]

    monkeypatch.setattr("app.rag.retriever.embed_texts", fake_embed_texts)
    monkeypatch.setattr("app.rag.retriever.cache_get", fake_cache_get)
    monkeypatch.setattr("app.rag.retriever.cache_set", fake_cache_set)
    monkeypatch.setattr("app.rag.retriever.KnowledgeChunkRepository", FakeRepo)

    result = await retrieve(None, "云龙湖门票多少钱")

    assert result.degraded is True
    assert result.low_confidence is True
    assert result.gate == RetrievalGate.WEAK
    assert result.chunks
    assert result.chunks[0].chunk_id == 9