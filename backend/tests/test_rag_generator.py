from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.clients.llm import LLMUnavailableError
from app.rag.generator import (
    GenerateResult,
    check_numeric_consistency,
    generate_answer,
    strip_sentences,
    validate_citations,
)
from app.rag.retriever import RetrievalGate, RetrievalResult


def _retrieval(gate: RetrievalGate, count: int = 3, low_confidence: bool = False) -> RetrievalResult:
    chunks = [
        SimpleNamespace(
            chunk_id=i,
            chunk_text=f"分块{i}内容",
            similarity=0.8 if gate != RetrievalGate.INSUFFICIENT else 0.3,
            metadata={"doc_title": f"文档{i}", "topic": "两汉文化", "source": "官网"},
        )
        for i in range(1, count + 1)
    ]
    return RetrievalResult(
        chunks=chunks if gate != RetrievalGate.INSUFFICIENT else [],
        gate=gate,
        max_similarity=0.8 if gate != RetrievalGate.INSUFFICIENT else 0.3,
        low_confidence=low_confidence,
    )


async def test_insufficient_gate_refuses_without_llm():
    with patch("app.rag.generator.text_call") as mocked:
        result = await generate_answer("量子力学在徐州的应用", _retrieval(RetrievalGate.INSUFFICIENT))
    assert result.refused is True
    assert "没有找到可靠资料" in result.answer
    mocked.assert_not_called()


async def test_refusal_output_passthrough():
    with patch(
        "app.rag.generator.text_call",
        return_value="暂时没有找到可靠资料，建议您换个问法，或咨询景区官方渠道",
    ):
        result = await generate_answer("问题", _retrieval(RetrievalGate.HIT))
    assert result.refused is True
    assert "没有找到可靠资料" in result.answer


async def test_low_confidence_prefix_added():
    with patch(
        "app.rag.generator.text_call",
        return_value="龟山汉墓是西汉楚王墓。[1] 文档1 · 官网\n【来源】\n[1] 文档1 · 官网",
    ):
        result = await generate_answer("汉墓", _retrieval(RetrievalGate.WEAK, low_confidence=True))
    assert result.answer.startswith("以下信息可能与您的问题部分相关")
    assert result.low_confidence is True


async def test_llm_failure_returns_busy_message():
    with patch(
        "app.rag.generator.text_call",
        side_effect=LLMUnavailableError("LLM 服务不可用"),
    ):
        result = await generate_answer("汉墓", _retrieval(RetrievalGate.HIT))
    assert result.answer == "服务繁忙，请稍后重试。"


async def test_citation_validation_strips_invalid_and_regenerates():
    fake_answers = [
        "龟山汉墓很有名。[7] 不存在的来源\n【来源】\n[7] 幻觉文档 · 幻觉出处",
        "龟山汉墓很有名。[1] 文档1 · 官网\n【来源】\n[1] 文档1 · 官网",
    ]
    with patch("app.rag.generator.text_call", side_effect=fake_answers):
        result = await generate_answer("汉墓", _retrieval(RetrievalGate.HIT))
    assert result.refused is False
    assert result.citations == [1]
    assert result.regenerated is True


def test_validate_citations_bounds():
    answer = "内容 [1] 与 [2] 还有 [9] 引用"
    assert validate_citations(answer, 3) == [1, 2]


def test_numeric_consistency_detects_conflict():
    answer = "龟山汉墓门票 99 元，值得游览。开放时间另见。"
    facts = {"龟山汉墓门票": "60 元"}
    bad = check_numeric_consistency(answer, facts)
    assert len(bad) == 1
    ok_answer = "龟山汉墓门票 60 元，值得游览。"
    assert check_numeric_consistency(ok_answer, facts) == []


def test_strip_sentences_removes_conflicting():
    answer = "第一句没问题。\n门票 99 元这句要删。\n最后一句保留。"
    stripped = strip_sentences(answer, ["门票 99 元这句要删。"])
    assert "要删" not in stripped
    assert "第一句" in stripped
    assert "最后一句" in stripped


async def test_numeric_conflict_strips_sentence_after_retry():
    conflicting = "龟山汉墓门票 99 元，值得游览。[1] 文档1 · 官网"
    with patch("app.rag.generator.text_call", return_value=conflicting):
        result = await generate_answer(
            "龟山汉墓门票", _retrieval(RetrievalGate.HIT), {"龟山汉墓门票": "60 元"}
        )
    assert result.regenerated is True
    assert "99" not in result.answer or result.stripped_sentences