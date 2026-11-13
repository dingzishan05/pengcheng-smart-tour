from __future__ import annotations

from app.agent.state import AgentError, AgentResponse, SourceRef, TourState
from app.core.logging import get_logger
from app.rag.generator import GenerateResult, generate_answer
from app.rag.prompts import REFUSAL_MESSAGE
from app.rag.retriever import RetrievedChunk, RetrievalGate, RetrievalResult
from app.repositories.poi_repo import PoiRepository
from app.tools.knowledge import (
    KnowledgeQuery,
    KnowledgeRetrievalOutput,
    RetrievedChunkInfo,
    retrieve_xuzhou_knowledge,
)

logger = get_logger("app.agent.rag_qa")

GO_INTENT_PATTERNS = ("就去这", "那就去", "安排一下", "带我去", "想去这", "帮我规划", "帮我安排", "想去玩")

REFUSAL_RESPONSE = (
    f"{REFUSAL_MESSAGE}您也可以直接告诉我游玩需求（时长、预算、兴趣），我来为您规划行程。"
)


def _to_retrieval_result(output: KnowledgeRetrievalOutput) -> RetrievalResult:
    chunks = [
        RetrievedChunk(
            chunk_id=c.chunk_id,
            chunk_text=c.chunk_text,
            similarity=c.similarity,
            metadata={
                "doc_title": c.doc_title,
                "topic": c.topic,
                "source": c.source,
                "version": c.doc_version,
            },
        )
        for c in output.chunks
    ]
    gate = RetrievalGate.INSUFFICIENT if output.insufficient else (
        RetrievalGate.WEAK if output.low_confidence else RetrievalGate.HIT
    )
    return RetrievalResult(
        chunks=chunks,
        gate=gate,
        low_confidence=output.low_confidence,
        degraded=output.degraded,
        expanded_query=output.expanded_query,
    )


def _detect_poi_in_question(question: str, poi_names: list[str]) -> str | None:
    for name in poi_names:
        if len(name) >= 2 and name in question:
            return name
    return None


def _sources_from(output: KnowledgeRetrievalOutput, gen: GenerateResult) -> list[SourceRef]:
    cited = set(gen.citations or [])
    if not cited:
        return [SourceRef(doc_title=c.doc_title, source=c.source) for c in output.chunks[:3]]
    return [
        SourceRef(doc_title=c.doc_title, source=c.source)
        for idx, c in enumerate(output.chunks, start=1)
        if idx in cited
    ]


def make_rag_qa_node(session_factory, answer_fn=None):
    answer = answer_fn or generate_answer

    async def rag_qa_node(state: TourState) -> dict:
        question = state.get("user_message", "")
        try:
            async with session_factory() as session:
                result = await retrieve_xuzhou_knowledge(session, KnowledgeQuery(question=question))
                poi_names: list[str] = []
                try:
                    pois = await PoiRepository(session).list_active()
                    poi_names = [p.name for p in pois]
                except Exception:
                    poi_names = []
        except Exception as exc:
            logger.error("rag_qa retrieval failed", extra={"kind": "external", "error": str(exc)[:200]})
            return {
                "error": AgentError(code="50002", message="知识检索失败"),
                "final_response": AgentResponse(response_type="fallback", content="知识服务暂时不可用，请稍后重试。"),
            }

        if not result.ok or result.data is None:
            return {
                "error": AgentError(code="50002", message="知识检索失败"),
                "final_response": AgentResponse(response_type="fallback", content="知识服务暂时不可用，请稍后重试。"),
            }

        output: KnowledgeRetrievalOutput = result.data
        update: dict = {"rag_context": list(output.chunks)}

        if output.insufficient or not output.chunks:
            update["final_response"] = AgentResponse(
                response_type="fallback", content=REFUSAL_RESPONSE
            )
            return update

        retrieval = _to_retrieval_result(output)
        gen = await answer(question, retrieval)
        update["final_response"] = AgentResponse(
            response_type="knowledge_answer",
            content=gen.answer,
            sources=_sources_from(output, gen),
        )

        if any(p in question for p in GO_INTENT_PATTERNS):
            poi_name = _detect_poi_in_question(question, poi_names)
            if poi_name is not None:
                update["travel_intent_poi"] = poi_name
                request = state.get("structured_request")
                if request is not None:
                    pinned = list(request.pinned_pois)
                    if poi_name not in pinned:
                        pinned.append(poi_name)
                    request.pinned_pois = pinned
                    update["structured_request"] = request
                    update["missing_fields"] = [
                        f for f in (state.get("missing_fields") or []) if f in ("duration", "budget")
                    ]
                    update["intent"] = "itinerary_request"
        return update

    rag_qa_node.__name__ = "rag_qa"
    return rag_qa_node