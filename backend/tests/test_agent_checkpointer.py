from __future__ import annotations

from app.agent.checkpointer import checkpoint_serializer, clear_thread
from app.agent.state import AgentResponse, MessageRecord, StructuredRequest
from app.optimizer.models import ItineraryPlan, RouteEdge, RouteOption


def test_serializer_roundtrip_agent_types():
    serde = checkpoint_serializer()
    payload = {
        "structured": StructuredRequest(duration_hours=8, budget=300, interests=["历史"]),
        "message": MessageRecord(role="user", content="一日游"),
        "response": AgentResponse(response_type="plans", content="ok"),
        "edge": RouteEdge(walk=RouteOption(minutes=10, distance_meters=800)),
        "plan": ItineraryPlan(goal="balanced"),
    }
    dumped = serde.dumps_typed(payload)
    loaded = serde.loads_typed(dumped)
    assert loaded["structured"].budget == 300
    assert loaded["message"].content == "一日游"
    assert loaded["response"].response_type == "plans"
    assert loaded["edge"].walk.minutes == 10
    assert loaded["plan"].goal == "balanced"


async def test_clear_thread_delegates():
    calls = []

    class FakeSaver:
        async def adelete_thread(self, thread_id):
            calls.append(thread_id)

    await clear_thread(FakeSaver(), "conv-1")
    assert calls == ["conv-1"]


async def test_clear_thread_swallows_errors():
    class BrokenSaver:
        async def adelete_thread(self, thread_id):
            raise RuntimeError("boom")

    await clear_thread(BrokenSaver(), "conv-1")