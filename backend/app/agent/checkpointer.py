from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from app.agent.state import (
    AgentError,
    AgentResponse,
    MessageRecord,
    OriginInfo,
    SourceRef,
    StructuredRequest,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.optimizer.models import (
    InfeasibilityAdvice,
    InfeasibleReason,
    InfeasibilityReport,
    InfeasibleReasonKind,
    ItineraryItem,
    ItineraryPlan,
    RouteEdge,
    RouteOption,
    TransportSegment,
    WeatherInfo,
)
from app.tools.base import ToolError, ToolErrorKind, ToolMeta, ToolResult
from app.tools.knowledge import RetrievedChunkInfo
from app.tools.live import RouteOptionInfo, WeatherSnapshot
from app.tools.pois import Coordinate, PoiCandidate, TimeWindow

logger = get_logger("app.agent.checkpointer")

CHECKPOINT_TABLES_SETUP = False

ALLOWED_MSGPACK_TYPES = (
    AgentError,
    AgentResponse,
    MessageRecord,
    OriginInfo,
    SourceRef,
    StructuredRequest,
    InfeasibilityAdvice,
    InfeasibleReason,
    InfeasibilityReport,
    InfeasibleReasonKind,
    ItineraryItem,
    ItineraryPlan,
    RouteEdge,
    RouteOption,
    TransportSegment,
    WeatherInfo,
    ToolError,
    ToolErrorKind,
    ToolMeta,
    ToolResult,
    RetrievedChunkInfo,
    RouteOptionInfo,
    WeatherSnapshot,
    Coordinate,
    PoiCandidate,
    TimeWindow,
)


def checkpoint_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=ALLOWED_MSGPACK_TYPES)


async def build_checkpointer() -> BaseCheckpointSaver:
    """PostgreSQL 优先（thread_id=conversation_id），未安装独立包时回退内存。"""
    global CHECKPOINT_TABLES_SETUP
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        saver = AsyncPostgresSaver.from_conn_string(
            settings.database_url, serde=checkpoint_serializer()
        )
        if not CHECKPOINT_TABLES_SETUP:
            await saver.setup()
            CHECKPOINT_TABLES_SETUP = True
        logger.info("postgres checkpointer enabled")
        return saver
    except ImportError:
        logger.warning(
            "langgraph-checkpoint-postgres not installed, fallback to InMemorySaver",
            extra={"kind": "algorithm"},
        )
        return InMemorySaver(serde=checkpoint_serializer())


async def clear_thread(checkpointer: BaseCheckpointSaver, thread_id: str) -> None:
    """会话删除时级联清理图状态（消息/草稿清理由领域服务负责）。"""
    try:
        await checkpointer.adelete_thread(thread_id)
    except Exception as exc:
        logger.error(
            "clear_thread failed",
            extra={"kind": "algorithm", "thread_id": thread_id, "error": str(exc)[:200]},
        )