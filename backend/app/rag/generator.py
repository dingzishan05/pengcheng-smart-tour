from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.rag.prompts import (
    LOW_CONFIDENCE_PREFIX,
    RAG_ANSWER_PROMPT_VERSION,
    RAG_ANSWER_SYSTEM_PROMPT,
    RAG_ANSWER_USER_TEMPLATE,
    REFUSAL_MESSAGE,
)
from app.clients.llm import LLMUnavailableError, text_call
from app.core.logging import get_logger
from app.rag.retriever import RetrievalGate, RetrievalResult

logger = get_logger("app.rag.generator")

CITATION_PATTERN = re.compile(r"\[(\d+)\]")


@dataclass
class GenerateResult:
    answer: str
    refused: bool = False
    low_confidence: bool = False
    prompt_version: str = RAG_ANSWER_PROMPT_VERSION
    citations: list[int] = field(default_factory=list)
    stripped_sentences: list[str] = field(default_factory=list)
    regenerated: bool = False


def build_context(chunks) -> str:
    lines = []
    for idx, chunk in enumerate(chunks, start=1):
        meta = chunk.metadata or {}
        lines.append(
            f"[{idx}] 《{meta.get('doc_title', '未知文档')}》（{meta.get('topic', '')}）\n{chunk.chunk_text}\n"
            f"（出处：{meta.get('source', '未注明')}）"
        )
    return "\n\n".join(lines)


def build_facts_block(structured_facts: dict | None) -> str:
    if not structured_facts:
        return ""
    lines = ["【系统数据】（门票价格与开放时间以此为准）"]
    for name, value in structured_facts.items():
        lines.append(f"- {name}：{value}")
    return "\n".join(lines) + "\n"


def validate_citations(answer: str, chunk_count: int) -> list[int]:
    cited = {int(m) for m in CITATION_PATTERN.findall(answer)}
    valid = sorted(c for c in cited if 1 <= c <= chunk_count)
    return valid


def check_numeric_consistency(answer: str, structured_facts: dict | None) -> list[str]:
    if not structured_facts:
        return []
    inconsistent: list[str] = []
    for name, value in structured_facts.items():
        value_str = str(value)
        normalized = re.sub(r"[（）()]", "", value_str)
        key_numbers = re.findall(r"\d+(?:\.\d+)?", normalized)
        if not key_numbers:
            continue
        for sentence in re.split(r"(?<=[。；;])", answer):
            if name in sentence:
                sentence_numbers = set(re.findall(r"\d+(?:\.\d+)?", sentence))
                if not all(n in sentence_numbers for n in key_numbers):
                    inconsistent.append(sentence.strip())
    return inconsistent


def strip_sentences(answer: str, sentences: list[str]) -> str:
    result = answer
    for sentence in sentences:
        result = result.replace(sentence, "")
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result


async def generate_answer(
    query: str,
    retrieval: RetrievalResult,
    structured_facts: dict | None = None,
) -> GenerateResult:
    if retrieval.gate == RetrievalGate.INSUFFICIENT or not retrieval.chunks:
        return GenerateResult(answer=REFUSAL_MESSAGE, refused=True)

    context = build_context(retrieval.chunks)
    facts_block = build_facts_block(structured_facts)
    system_prompt = RAG_ANSWER_SYSTEM_PROMPT.format(context=context, facts_block=facts_block)
    user_prompt = RAG_ANSWER_USER_TEMPLATE.format(question=query)

    low_confidence = retrieval.low_confidence
    answer = ""
    regenerated = False
    stripped: list[str] = []

    for attempt in (1, 2):
        try:
            answer = await text_call(system_prompt, user_prompt, temperature=0.2)
        except LLMUnavailableError:
            logger.warning("rag answer generation failed", extra={"kind": "external", "attempt": attempt})
            if attempt == 1:
                continue
            return GenerateResult(
                answer="服务繁忙，请稍后重试。",
                low_confidence=low_confidence,
            )

        if REFUSAL_MESSAGE.split("，")[0] in answer:
            return GenerateResult(answer=REFUSAL_MESSAGE, refused=True, low_confidence=low_confidence)

        if not validate_citations(answer, len(retrieval.chunks)):
            if attempt == 1:
                regenerated = True
                continue
            answer = REFUSAL_MESSAGE
            return GenerateResult(answer=answer, refused=True, low_confidence=low_confidence, regenerated=regenerated)

        inconsistent = check_numeric_consistency(answer, structured_facts)
        if inconsistent:
            if attempt == 1:
                regenerated = True
                continue
            stripped = inconsistent
            answer = strip_sentences(answer, inconsistent)
            if not validate_citations(answer, len(retrieval.chunks)):
                answer = strip_sentences(answer, []) + "\n" + _rebuild_sources(retrieval)
        break

    if low_confidence:
        answer = LOW_CONFIDENCE_PREFIX + answer

    return GenerateResult(
        answer=answer,
        low_confidence=low_confidence,
        citations=validate_citations(answer, len(retrieval.chunks)),
        stripped_sentences=stripped,
        regenerated=regenerated,
    )


def _rebuild_sources(retrieval: RetrievalResult) -> "str":
    lines = ["【来源】"]
    for idx, chunk in enumerate(retrieval.chunks, start=1):
        meta = chunk.metadata or {}
        lines.append(f"[{idx}] {meta.get('doc_title', '未知文档')} · {meta.get('source', '未注明')}")
    return "\n".join(lines)