from __future__ import annotations

import pytest

from app.rag.ingestion import (
    CHUNK_MAX_CHARS,
    IngestionError,
    chunk_content,
    clean_content,
    validate_document,
)


def test_validate_rejects_missing_source():
    with pytest.raises(IngestionError) as exc:
        validate_document("标题", "两汉文化", "这是一段足够长的正文内容，用于通过最小长度校验。", "")
    assert "来源" in str(exc.value)


def test_validate_rejects_bad_topic():
    with pytest.raises(IngestionError):
        validate_document("标题", "购物娱乐", "这是一段足够长的正文内容，用于通过最小长度校验。", "官网")


def test_validate_rejects_short_content():
    with pytest.raises(IngestionError):
        validate_document("标题", "两汉文化", "太短", "官网")


def test_validate_accepts_valid_document():
    validate_document("标题", "景点介绍", "这是一段足够长的正文内容，用于通过最小长度校验。", "官网 https://x")


def test_clean_content_dedups_and_normalizes():
    raw = "重复段落。\n重复段落。\n\n\n\n结尾段;"
    cleaned = clean_content(raw)
    assert "；" in cleaned
    assert cleaned.count("重复段落") == 1
    assert "\n\n\n" not in cleaned


def test_chunk_content_title_block_first():
    meta = {"doc_title": "龟山汉墓", "topic": "两汉文化", "source": "官网", "version": 1}
    drafts = chunk_content("龟山汉墓", "段落一内容。\n段落二内容。", meta)
    assert drafts[0].is_title is True
    assert drafts[0].chunk_text == "龟山汉墓"
    assert drafts[0].metadata["doc_title"] == "龟山汉墓"
    assert all(not d.is_title for d in drafts[1:])
    seqs = [d.chunk_seq for d in drafts]
    assert seqs == list(range(len(drafts)))


def test_chunk_content_respects_max_chars():
    paragraphs = [f"这是第{i}个测试段落，包含一些文字用于撑长度。" * 5 for i in range(20)]
    content = "\n".join(paragraphs)
    meta = {"doc_title": "t", "topic": "景点介绍", "source": "s", "version": 1}
    drafts = chunk_content("t", content, meta)
    body = [d for d in drafts if not d.is_title]
    assert len(body) > 1
    for draft in body:
        assert len(draft.chunk_text) <= CHUNK_MAX_CHARS + 120


def test_chunk_content_overlap_between_adjacent():
    content = "\n".join(f"段落{i}：" + "重叠测试内容。" * 40 for i in range(6))
    meta = {"doc_title": "t", "topic": "景点介绍", "source": "s", "version": 1}
    drafts = chunk_content("t", content, meta)
    body = [d for d in drafts if not d.is_title]
    if len(body) >= 2:
        assert body[1].chunk_text.startswith(body[0].chunk_text[-80:] or "")


def test_chunk_metadata_complete():
    meta = {"doc_title": "标题", "topic": "交通常识", "source": "铁路官网", "version": 3}
    drafts = chunk_content("标题", "徐州地铁已开通三条线路。", meta)
    for draft in drafts:
        assert draft.metadata["doc_title"] == "标题"
        assert draft.metadata["topic"] == "交通常识"
        assert draft.metadata["source"] == "铁路官网"
        assert draft.metadata["version"] == 3
        assert "section" in draft.metadata