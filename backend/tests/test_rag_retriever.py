from __future__ import annotations

from app.rag.retriever import (
    RetrievalGate,
    evaluate_gate,
    preprocess_query,
    query_hash,
)
from app.core.config import settings


def test_preprocess_removes_stop_words():
    assert "吗" not in preprocess_query("请问龟山汉墓怎么走吗")
    assert "请问" not in preprocess_query("请问龟山汉墓怎么走吗")
    assert "龟山汉墓" in preprocess_query("请问龟山汉墓怎么走吗")


def test_preprocess_synonym_expansion():
    expanded = preprocess_query("汉墓有什么看点")
    assert "龟山汉墓" in expanded
    assert "狮子山楚王陵" in expanded


def test_preprocess_keeps_plain_query():
    assert "云龙湖" in preprocess_query("云龙湖好玩吗")
    assert preprocess_query("云龙湖") == "云龙湖"


def test_query_hash_stable():
    assert query_hash("龟山汉墓") == query_hash("龟山汉墓")
    assert query_hash("龟山汉墓") != query_hash("云龙湖")


def test_evaluate_gate_thresholds():
    hit = settings.rag_similarity_threshold
    weak = settings.rag_weak_threshold
    assert evaluate_gate(hit) == RetrievalGate.HIT
    assert evaluate_gate(hit + 0.01) == RetrievalGate.HIT
    assert evaluate_gate((hit + weak) / 2) == RetrievalGate.WEAK
    assert evaluate_gate(weak - 0.01) == RetrievalGate.INSUFFICIENT
    assert evaluate_gate(0.0) == RetrievalGate.INSUFFICIENT