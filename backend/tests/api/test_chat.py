from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.agent import runtime
from app.agent.state import AgentError, AgentResponse, StructuredRequest
from app.main import app
from app.optimizer.models import (
    Goal,
    ItineraryItem as PlanItem,
    ItineraryPlan,
    TransportMode,
    TransportSegment,
)

client = TestClient(app)


class FakeGraph:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[tuple[dict, dict | None]] = []

    async def ainvoke(self, state, config=None):
        self.calls.append((state, config))
        return self.result


class FakeConvRepo:
    conversations: list = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.conversations = []

    async def get(self, conv_id):
        return next((c for c in self.conversations if c.id == conv_id), None)

    async def list_by_token(self, token):
        return [c for c in self.conversations if c.session_token == token]

    async def create(self, token, title=None):
        conv = SimpleNamespace(
            id=len(self.conversations) + 1,
            session_token=token,
            title=title,
            status="active",
            last_active_at=None,
        )
        self.conversations.append(conv)
        return conv

    async def touch(self, conv, title=None):
        if title is not None and conv.title is None:
            conv.title = title
        return None

    async def soft_delete(self, conv):
        conv.status = "deleted"
        conv.deleted_at = datetime.now(timezone.utc)
        return None


class FakeMsgRepo:
    messages: list = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.messages = []

    async def append(self, conversation_id, role, content, response_type=None, plan_refs=None):
        msg = SimpleNamespace(
            id=len(self.messages) + 1,
            conversation_id=conversation_id,
            role=role,
            content=content,
            response_type=response_type,
            plan_refs=plan_refs,
            sent_at=None,
        )
        self.messages.append(msg)
        return msg


class FakeItinRepo:
    itineraries: list = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.itineraries = []

    async def add(self, itin):
        itin.id = len(self.itineraries) + 1
        self.itineraries.append(itin)
        return itin

    async def list_by_conversation(self, conversation_id):
        return [i for i in self.itineraries if i.conversation_id == conversation_id]

    async def get_for_conversation(self, conversation_id, itinerary_id):
        return next(
            (
                i
                for i in self.itineraries
                if i.id == itinerary_id and i.conversation_id == conversation_id
            ),
            None,
        )


class FakeItemRepo:
    by_itinerary: dict = {}

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.by_itinerary = {}

    async def bulk_add(self, items):
        if not items:
            return
        self.by_itinerary.setdefault(items[0].itinerary_id, []).extend(items)

    async def list_by_itinerary(self, itinerary_id):
        return list(self.by_itinerary.get(itinerary_id, []))


class FakeEventRepo:
    events: list = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.events = []

    async def append(self, **kwargs):
        self.events.append(kwargs)
        return kwargs


class FakeSessionCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def commit(self):
        return None


@pytest.fixture()
def chat_env(monkeypatch):
    FakeConvRepo.reset()
    FakeMsgRepo.reset()
    FakeItinRepo.reset()
    FakeItemRepo.reset()
    FakeEventRepo.reset()
    monkeypatch.setattr("app.api.v1.chat.ConversationRepository", FakeConvRepo)
    monkeypatch.setattr("app.api.deps.ConversationRepository", FakeConvRepo)
    monkeypatch.setattr("app.api.v1.chat.MessageRepository", FakeMsgRepo)
    monkeypatch.setattr("app.api.v1.chat.ItineraryRepository", FakeItinRepo)
    monkeypatch.setattr("app.api.v1.chat.ItineraryItemRepository", FakeItemRepo)
    monkeypatch.setattr("app.api.v1.chat.ChangeEventRepository", FakeEventRepo)
    monkeypatch.setattr(
        "app.api.v1.chat.get_session_factory", lambda: (lambda: FakeSessionCtx())
    )
    app.dependency_overrides[
        __import__("app.api.deps", fromlist=["get_session_factory"]).get_session_factory
    ] = lambda: (lambda: FakeSessionCtx())
    yield monkeypatch
    runtime.reset_graph()
    app.dependency_overrides.clear()


def make_three_plans():
    def one(goal, cost, transit):
        item = PlanItem(
            poi_id=1,
            name="汉文化景区",
            kind="sight",
            start_minute=540,
            end_minute=690,
            ticket_price=cost,
        )
        segment = TransportSegment(
            from_poi_id=-1,
            to_poi_id=1,
            mode=TransportMode.TRANSIT,
            minutes=transit,
            fare=2,
        )
        return ItineraryPlan(
            goal=goal,
            items=[item],
            segments=[segment],
            total_cost=cost,
            total_minutes=540,
            transit_minutes=transit,
        )

    return [one(Goal.SAVE_MONEY, 188, 95), one(Goal.SAVE_TIME, 320, 60), one(Goal.BALANCED, 240, 80)]


def test_chat_plans_flow_demo_replay(chat_env):
    plans = make_three_plans()
    request = StructuredRequest(
        duration_hours=8,
        budget=300,
        interests=["历史文化"],
        visit_date=__import__("datetime").date(2026, 10, 21),
    )
    graph = FakeGraph(
        {
            "final_response": AgentResponse(
                response_type="plans",
                content="已为您规划好三种方案",
                plans=plans,
                data_time=None,
            ),
            "structured_request": request,
            "visit_date": "2026-10-21",
        }
    )
    runtime.set_graph(graph)

    response = client.post(
        "/api/v1/chat",
        json={"message": "我第一次来徐州，只有一天，预算300元，喜欢历史文化"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["response_type"] == "plans"
    assert payload["conversation_id"]
    assert len(payload["plans"]) == 3
    summary = payload["plans"][0]
    assert summary["plan_type"] == "省钱"
    assert summary["total_cost"] == "188.00"
    assert summary["total_minutes"] == 540
    assert summary["transport_minutes"] == 95
    assert summary["highlights"] and summary["suited_for"]
    assert payload["plan_details_available"] is True
    assert payload["code"] is None
    assert "pczy_session" in response.cookies

    roles = [m.role for m in FakeMsgRepo.messages]
    assert roles == ["user", "system"]
    assert FakeMsgRepo.messages[0].content.startswith("我第一次来徐州")
    assert len(FakeItinRepo.itineraries) == 3
    assert all(i.status == "草稿" for i in FakeItinRepo.itineraries)
    assert graph.calls[0][0]["user_message"].startswith("我第一次来徐州")
    assert graph.calls[0][1]["configurable"]["thread_id"] == payload["conversation_id"]


def test_chat_sets_conversation_cookie_and_reuse(chat_env):
    graph = FakeGraph(
        {
            "final_response": AgentResponse(response_type="clarify", content="请问您的预算大概是多少？"),
        }
    )
    runtime.set_graph(graph)
    first = client.post("/api/v1/chat", json={"message": "想去徐州玩"})
    assert first.status_code == 200
    token = first.cookies["pczy_session"]
    assert token

    second = client.post(
        "/api/v1/chat",
        json={"conversation_id": first.json()["conversation_id"], "message": "预算300元"},
        cookies={"pczy_session": token},
    )
    assert second.status_code == 200
    assert second.json()["conversation_id"] == first.json()["conversation_id"]
    assert len(FakeConvRepo.conversations) == 1


def test_chat_clarify_response(chat_env):
    runtime.set_graph(
        FakeGraph(
            {
                "final_response": AgentResponse(response_type="clarify", content="请问您打算玩几天？"),
            }
        )
    )
    response = client.post("/api/v1/chat", json={"message": "想去徐州玩"})
    payload = response.json()
    assert payload["response_type"] == "clarify"
    assert payload["content"] == "请问您打算玩几天？"
    assert payload["plans"] is None
    assert payload["plan_details_available"] is False


def test_chat_knowledge_answer_with_sources(chat_env):
    from app.agent.state import SourceRef

    runtime.set_graph(
        FakeGraph(
            {
                "final_response": AgentResponse(
                    response_type="knowledge_answer",
                    content="龟山汉墓是西汉楚襄王刘注的夫妇合葬墓。",
                    sources=[SourceRef(doc_title="龟山汉墓介绍", source="景区官方")],
                ),
            }
        )
    )
    response = client.post("/api/v1/chat", json={"message": "龟山汉墓是什么"})
    payload = response.json()
    assert payload["response_type"] == "knowledge_answer"
    assert payload["sources"][0]["doc_title"] == "龟山汉墓介绍"


def test_chat_llm_failure_returns_50003_and_saves_input(chat_env):
    runtime.set_graph(
        FakeGraph(
            {
                "error": AgentError(code="50003", message="LLM 服务不可用"),
                "final_response": AgentResponse(
                    response_type="fallback",
                    content="系统开小差了，请稍后重试。",
                ),
            }
        )
    )
    response = client.post("/api/v1/chat", json={"message": "帮我规划一天的行程"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 50003
    assert "已保存" in payload["message"]
    assert payload["response_type"] == "fallback"
    user_messages = [m for m in FakeMsgRepo.messages if m.role == "user"]
    assert len(user_messages) == 1
    assert user_messages[0].content == "帮我规划一天的行程"

    runtime.set_graph(
        FakeGraph(
            {
                "final_response": AgentResponse(response_type="fallback", content="好的，已收到。"),
            }
        )
    )
    retry = client.post("/api/v1/chat", json={"message": "帮我规划一天的行程"})
    assert retry.status_code == 200
    assert retry.json()["code"] is None


def test_chat_empty_message_rejected(chat_env):
    response = client.post("/api/v1/chat", json={"message": ""})
    assert response.status_code == 422
    assert response.json()["code"] == 40001


def test_chat_blank_message_rejected(chat_env):
    response = client.post("/api/v1/chat", json={"message": "   "})
    assert response.status_code == 422


def test_chat_too_long_message_rejected(chat_env):
    response = client.post("/api/v1/chat", json={"message": "长" * 2001})
    assert response.status_code == 422
    assert response.json()["code"] == 40001


def test_chat_unknown_conversation_rejected(chat_env):
    runtime.set_graph(
        FakeGraph({"final_response": AgentResponse(response_type="fallback", content="x")})
    )
    response = client.post(
        "/api/v1/chat",
        json={"conversation_id": "999", "message": "你好"},
        cookies={"pczy_session": str(uuid4())},
    )
    assert response.status_code == 404
    assert response.json()["code"] == 40401