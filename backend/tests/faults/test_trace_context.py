"""14.8 日志字段完整性：conversation_id 全链路贯通（design/10 §7 / DFX 4.4.2）。

验证链路：TraceMiddleware 生成/透传 cid → 业务路由把会话 conversation_id 写入日志上下文
→ 节点/工具日志经 JsonFormatter 自动携带 → 响应头 X-Conversation-Id 回传业务会话 ID。
"""

from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient

from app.agent import runtime
from app.agent.state import AgentResponse
from app.core.logging import JsonFormatter, conversation_id_var, get_logger
from app.main import app
from tests.api.test_chat import FakeGraph, chat_env

client = TestClient(app, raise_server_exceptions=False)


def test_json_formatter_injects_conversation_id_from_context():
    token = conversation_id_var.set("conv-42")
    try:
        logger = get_logger("test.trace")
        record = logging.LogRecord(
            "test.trace", logging.WARNING, __file__, 1, "节点执行", None, None
        )
        payload = json.loads(JsonFormatter().format(record))
        assert payload["conversation_id"] == "conv-42"
        assert payload["level"] == "WARNING"
        assert payload["kind"] == "system"
    finally:
        conversation_id_var.reset(token)


def test_trace_middleware_echoes_request_conversation_id():
    response = client.get("/api/v1/definitely-missing", headers={"X-Conversation-Id": "conv-echo"})
    assert response.headers["X-Conversation-Id"] == "conv-echo"


def test_chat_route_binds_business_conversation_id_end_to_end(chat_env, caplog):
    class LoggingGraph:
        async def ainvoke(self, state, config=None):
            get_logger("app.agent.fake_node").warning("graph node running")
            return {
                "final_response": AgentResponse(
                    response_type="knowledge_answer", content="参考知识库答复"
                )
            }

    runtime.set_graph(LoggingGraph())
    caplog.handler.setFormatter(JsonFormatter())

    with caplog.at_level(logging.INFO):
        response = client.post("/api/v1/chat", json={"message": "龟山汉墓门票多少钱"})

    assert response.status_code == 200
    business_conversation_id = response.json()["conversation_id"]
    assert response.headers["X-Conversation-Id"] == business_conversation_id

    json_lines = [json.loads(line) for line in caplog.text.splitlines() if line.strip()]
    node_logs = [line for line in json_lines if line.get("msg") == "graph node running"]
    access_logs = [
        line for line in json_lines if line.get("msg", "").startswith("POST /api/v1/chat")
    ]

    assert node_logs, "图节点日志应被捕获"
    assert all(line["conversation_id"] == business_conversation_id for line in node_logs)
    assert access_logs
    assert all(
        line["conversation_id"] == business_conversation_id for line in access_logs
    ), "access 日志应与业务会话 ID 一致（全链路贯通）"


def test_plans_route_returns_business_conversation_header(monkeypatch):
    from app.api.deps import get_session_factory
    from tests.api.test_chat import (
        FakeConvRepo,
        FakeEventRepo,
        FakeItinRepo,
        FakeItemRepo,
        FakeMsgRepo,
        FakeSessionCtx,
        make_three_plans,
    )
    from tests.api.test_plans_api import wire_generation_nodes

    for repo in (FakeConvRepo, FakeMsgRepo, FakeItinRepo, FakeItemRepo, FakeEventRepo):
        repo.reset()
    for module, name, target in [
        ("app.api.v1.plans", "ItineraryRepository", FakeItinRepo),
        ("app.api.v1.plans", "ItineraryItemRepository", FakeItemRepo),
        ("app.api.v1.plans", "ChangeEventRepository", FakeEventRepo),
    ]:
        monkeypatch.setattr(f"{module}.{name}", target)
    monkeypatch.setattr("app.api.deps.ConversationRepository", FakeConvRepo)
    app.dependency_overrides[get_session_factory] = lambda: (lambda: FakeSessionCtx())
    try:
        wire_generation_nodes(
            monkeypatch,
            make_three_plans(),
            AgentResponse(response_type="plans", content="三套方案已生成"),
        )
        response = client.post(
            "/api/v1/plans",
            json={"request": {"duration_hours": 8, "budget": 300, "interests": ["历史文化"]}},
        )
        assert response.status_code == 200, response.text
        assert response.headers["X-Conversation-Id"] == response.json()["conversation_id"]
    finally:
        app.dependency_overrides.clear()