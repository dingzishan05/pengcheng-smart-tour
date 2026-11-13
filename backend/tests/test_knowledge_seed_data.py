from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.seed.knowledge_data import KNOWLEDGE_DOCUMENTS
from scripts.seed.seed_knowledge import validate_seed_data

FORBIDDEN_PRICE_PATTERNS = ("门票", "票价", "开放时间", "营业时间")


def test_seed_data_valid():
    assert validate_seed_data() == []


def test_seed_data_topic_coverage():
    by_topic: dict[str, int] = {}
    for doc in KNOWLEDGE_DOCUMENTS:
        by_topic[doc["topic"]] = by_topic.get(doc["topic"], 0) + 1
    assert by_topic["两汉文化"] >= 8
    assert by_topic["景点介绍"] >= 15
    assert by_topic["特色美食"] >= 5
    assert by_topic["交通常识"] >= 3


def test_seed_data_titles_unique():
    titles = [doc["title"] for doc in KNOWLEDGE_DOCUMENTS]
    assert len(titles) == len(set(titles))


def test_seed_data_sources_traceable():
    for doc in KNOWLEDGE_DOCUMENTS:
        source = doc["source"]
        assert source
        assert ("http" in source) or ("官网" in source) or ("百科" in source) or ("官方" in source)


def test_seed_data_avoids_duplicated_facts():
    for doc in KNOWLEDGE_DOCUMENTS:
        for pattern in FORBIDDEN_PRICE_PATTERNS:
            assert pattern not in doc["content"], (
                f"{doc['title']} 正文包含结构性事实词「{pattern}」，违反单一事实源原则"
            )