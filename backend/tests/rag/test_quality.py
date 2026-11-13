"""RAG 检索质量测试（tasks 14.4 / design/04 §10 测试要点全覆盖）。

要点映射：
1. 知识内/知识外问题集各 ≥50 条：命中率 ≥90%、知识外误答率 ≤5%；
2. 数字一致性：答案价格 vs 结构化价格（批量事实回归）；
3. 分块质量：标题类查询、长文档跨块召回；
4. 降级链路：向量服务失败 → 关键词检索通道可用且答案带低置信前缀；
5. 版本更新后旧版本分块不可检索、新版本即时生效。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.clients.llm import EmbeddingUnavailableError
from app.core.config import settings
from app.rag.generator import check_numeric_consistency, generate_answer
from app.rag.retriever import RetrievalGate, retrieve
from tests.rag.quality_sim import (
    IN_DOMAIN_QUERIES,
    NUMERIC_FACT_CASES,
    OUT_DOMAIN_QUERIES,
    build_seed_index,
    install_index,
)

HIT_RATE_MIN = 0.90
FALSE_ANSWER_MAX = 0.05
TOP1_PRECISION_MIN = 0.90


def test_dataset_sizes_meet_minimum():
    assert len(IN_DOMAIN_QUERIES) >= 50
    assert len(OUT_DOMAIN_QUERIES) >= 50


async def test_in_domain_hit_rate(monkeypatch):
    index = build_seed_index()
    install_index(monkeypatch, index)

    hits = 0
    top1_correct = 0
    failures: list[str] = []
    for query, allowed_titles in IN_DOMAIN_QUERIES:
        result = await retrieve(None, query)
        if result.gate == RetrievalGate.HIT:
            hits += 1
        elif result.gate == RetrievalGate.WEAK:
            failures.append(f"WEAK {query} sim={result.max_similarity:.2f}")
        if result.chunks and result.chunks[0].metadata.get("doc_title") in allowed_titles:
            top1_correct += 1
        else:
            failures.append(f"TOP1 {query} -> {result.chunks[0].metadata.get('doc_title') if result.chunks else None}")

    total = len(IN_DOMAIN_QUERIES)
    assert hits / total >= HIT_RATE_MIN, f"hit={hits}/{total}: {failures[:8]}"
    assert top1_correct / total >= TOP1_PRECISION_MIN, f"top1={top1_correct}/{total}: {failures[:8]}"


async def test_out_domain_refusal_rate(monkeypatch):
    index = build_seed_index()
    install_index(monkeypatch, index)

    false_answers: list[str] = []
    for query in OUT_DOMAIN_QUERIES:
        result = await retrieve(None, query)
        if result.gate == RetrievalGate.HIT:
            false_answers.append(f"{query} sim={result.max_similarity:.2f}")

    total = len(OUT_DOMAIN_QUERIES)
    assert len(false_answers) / total <= FALSE_ANSWER_MAX, false_answers


async def test_title_query_hits_title_doc(monkeypatch):
    index = build_seed_index()
    install_index(monkeypatch, index)

    for query, expected in [
        ("龟山汉墓", ("龟山汉墓：千古奇观", "龟山汉墓")),
        ("云龙湖", ("云龙湖",)),
        ("徐州博物馆", ("徐州博物馆", "徐州博物馆与汉代文物")),
        ("地锅鸡", ("地锅鸡",)),
        ("徐州地铁概况", ("徐州地铁概况",)),
        ("户部山古民居", ("户部山古民居",)),
    ]:
        result = await retrieve(None, query)
        assert result.gate == RetrievalGate.HIT, query
        assert result.chunks, query
        assert result.chunks[0].metadata["doc_title"] in expected, (
            query,
            result.chunks[0].metadata["doc_title"],
        )


def _long_doc() -> dict:
    filler = "考古部门对城墙基址进行了系统勘探，并结合地方志与舆图复核了历代修缮记录。"
    paragraphs = [
        "徐州古城墙始建于明代洪武年间，历经黄河水患与战乱损毁，现存遗迹分段分布于老城区边缘。" + filler * 5,
        "遗址中最具研究价值的是南门瓮城结构：瓮城平面呈半圆形，砖砌墙体保存完好，"
        "是国内罕见的明代瓮城实物，曾出土铁炮与守城器械。" + filler * 5,
        "北段城墙基址叠压清代加筑痕迹，展示了城垣不断加固的过程。" + filler * 5,
        "西段遗迹与回龙窝历史街区相邻，考古发掘揭示了城门与街巷的接续关系。" + filler * 5,
        "墙外护城河石堤以条石垒砌，堤岸走向与城垣完全平行，是研究明代水利防御的样本。" + filler * 5,
        "东段遗址已辟为开放绿地，市民可沿基址步道参观说明牌，了解城墙轮廓。" + filler * 5,
    ]
    return {
        "title": "徐州古城墙遗址考",
        "topic": "景点介绍",
        "content": "\n".join(paragraphs),
        "source": "市考古所发布资料（官方渠道白名单）",
        "status": "有效",
        "version": 1,
    }


async def test_long_doc_cross_chunk_recall(monkeypatch):
    index = build_seed_index([_long_doc()])
    install_index(monkeypatch, index)

    a = await retrieve(None, "南门瓮城")
    assert a.gate == RetrievalGate.HIT
    assert a.chunks[0].metadata["doc_title"] == "徐州古城墙遗址考"
    assert "瓮城" in a.chunks[0].chunk_text

    b = await retrieve(None, "护城河石堤")
    assert b.gate == RetrievalGate.HIT
    assert b.chunks[0].metadata["doc_title"] == "徐州古城墙遗址考"
    assert "护城河石堤" in b.chunks[0].chunk_text

    assert a.chunks[0].chunk_id != b.chunks[0].chunk_id

    combined = await retrieve(None, "瓮城 护城河石堤")
    assert combined.gate in (RetrievalGate.HIT, RetrievalGate.WEAK)
    texts = [c.chunk_text for c in combined.chunks]
    assert any("瓮城" in t for t in texts)
    assert any("护城河石堤" in t for t in texts)


async def test_embedding_failure_falls_back_to_keywords(monkeypatch):
    index = build_seed_index()
    install_index(monkeypatch, index)

    async def broken_embedding(query: str) -> list[float]:
        raise EmbeddingUnavailableError("embedding down")

    monkeypatch.setattr("app.rag.retriever.get_query_embedding", broken_embedding)

    result = await retrieve(None, "龟山汉墓")
    assert result.degraded is True
    assert result.gate == RetrievalGate.WEAK
    assert result.low_confidence is True
    assert result.chunks
    assert all("龟山汉墓" in c.chunk_text for c in result.chunks)

    with patch(
        "app.rag.generator.text_call",
        return_value="龟山汉墓是西汉楚王合葬墓。[1] 龟山汉墓 · 官方",
    ):
        gen = await generate_answer("龟山汉墓", result)
    assert gen.refused is False
    assert gen.low_confidence is True
    assert gen.answer.startswith("以下信息可能与您的问题部分相关")


async def test_version_update_old_not_retrievable(monkeypatch):
    v1 = {
        "title": "泠泉书院",
        "topic": "景点介绍",
        "content": "泠泉书院内有青铜镜陈列室，收藏仿古铜镜百余面。\n庭院以青瓦覆顶，环境清幽。",
        "source": "旧版资料（已失效）",
        "status": "失效",
        "version": 1,
    }
    v2 = {
        "title": "泠泉书院",
        "topic": "景点介绍",
        "content": "泠泉书院经过整修后重新开放，院内铺设彩色沥青跑道，并新增研学课堂。\n开放区域包括碑廊与茶室。",
        "source": "景区官方发布资料（官方渠道白名单）",
        "status": "有效",
        "version": 2,
    }
    index = build_seed_index([v1, v2])
    install_index(monkeypatch, index)

    stale_chunk_ids = {
        c.id for c in index.chunks if index.doc_by_id[c.document_id]["status"] == "失效"
    }
    stale = await retrieve(None, "泠泉书院 青铜镜陈列室")
    assert not (stale_chunk_ids & {c.chunk_id for c in stale.chunks})
    assert not any("青铜镜" in c.chunk_text for c in stale.chunks)

    fresh = await retrieve(None, "泠泉书院 彩色沥青跑道")
    assert fresh.gate == RetrievalGate.HIT
    assert fresh.chunks
    assert fresh.chunks[0].metadata["version"] == 2
    assert "彩色沥青跑道" in fresh.chunks[0].chunk_text


@pytest.mark.parametrize("name, fact, good_answer, bad_answer", NUMERIC_FACT_CASES)
def test_numeric_consistency_batch(name, fact, good_answer, bad_answer):
    facts = {name: fact}
    assert check_numeric_consistency(good_answer, facts) == []
    assert check_numeric_consistency(bad_answer, facts)


def test_similarity_thresholds_are_calibrated():
    assert settings.rag_similarity_threshold == 0.75
    assert settings.rag_weak_threshold == 0.60
    assert 0.0 < settings.rag_weak_threshold < settings.rag_similarity_threshold < 1.0