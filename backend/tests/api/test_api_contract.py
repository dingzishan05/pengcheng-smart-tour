"""14.5 API 集成测试：错误码映射、会话越权、行程状态机、审计、健康检查与 OpenAPI 契约。

覆盖缺口（对齐 tasks.md 14.5 与 design/06）：
- 13 个错误码 → HTTP 状态码映射与统一错误响应结构（40001—40902/50001—50005）
- 40101 SESSION_INVALID（refresh 无有效会话）与 refresh/confirm 会话归属越权 40401
- 行程状态机 API 层：REPLACED→confirm 40901、PENDING→CONFIRMED 端到端闭环
- 知识上传/编辑产生 ingest 审计日志
- GET /health 全可用 200 与 db/redis/scheduler 各自降级 503
- GET /admin/knowledge 列表过滤、字段契约与鉴权守卫
- POST /chat 响应 data_time 字段透传与落库
- OpenAPI 契约：核心路由全部注册
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent import runtime
from app.agent.state import AgentResponse
from app.core.errors import BizError, ErrorCode, install_exception_handlers
from app.main import app
from tests.api.test_admin_api import (
    FakeAuditRepo,
    FakeKnowledgeRepo,
    admin_env,
    login,
)
from tests.api.test_chat import (
    FakeEventRepo,
    FakeGraph,
    FakeItinRepo,
    FakeItemRepo,
    make_three_plans,
    chat_env,
)
from tests.api.test_plans_api import create_conversation, plans_env

client = TestClient(app, raise_server_exceptions=False)


# ---------- 错误码 → HTTP 状态码映射与统一响应结构 ----------


ERROR_CODE_STATUS_CASES = [
    (ErrorCode.PARAM_INVALID, 422),
    (ErrorCode.SESSION_INVALID, 401),
    (ErrorCode.ADMIN_UNAUTHORIZED, 401),
    (ErrorCode.FORBIDDEN, 403),
    (ErrorCode.NOT_FOUND, 404),
    (ErrorCode.STATE_CONFLICT, 409),
    (ErrorCode.DATA_CONFLICT, 409),
    (ErrorCode.NO_FEASIBLE_PLAN, 422),
    (ErrorCode.OPTIMIZE_TIMEOUT, 200),
    (ErrorCode.LLM_UNAVAILABLE, 200),
    (ErrorCode.EXTERNAL_DATA_UNAVAILABLE, 200),
    (ErrorCode.INTERNAL_ERROR, 500),
]


def make_biz_error_client(code: ErrorCode, **extra) -> TestClient:
    test_app = FastAPI()
    install_exception_handlers(test_app)

    @test_app.get("/boom")
    async def boom():
        raise BizError(code, "触发错误", **extra)

    return TestClient(test_app, raise_server_exceptions=False)


@pytest.mark.parametrize(("code", "expected_status"), ERROR_CODE_STATUS_CASES)
def test_error_code_http_mapping_and_uniform_shape(code, expected_status):
    response = make_biz_error_client(code).get("/boom")
    assert response.status_code == expected_status
    payload = response.json()
    assert payload["code"] == int(code)
    assert isinstance(payload["message"], str) and payload["message"]
    assert set(payload) <= {"code", "message", "detail", "session_id", "plan_id"}


def test_biz_error_payload_extras_present():
    response = make_biz_error_client(
        ErrorCode.STATE_CONFLICT,
        detail={"advice": "放宽预算"},
        session_id="7",
        plan_id="5",
    ).get("/boom")
    payload = response.json()
    assert payload["detail"] == {"advice": "放宽预算"}
    assert payload["session_id"] == "7"
    assert payload["plan_id"] == "5"


def test_unknown_route_maps_to_40401():
    response = client.get("/api/v1/definitely-missing")
    assert response.status_code == 404
    payload = response.json()
    assert payload["code"] == 40401
    assert isinstance(payload["message"], str)


def test_request_validation_maps_to_40001_with_detail_errors():
    response = client.post("/api/v1/admin/login", json={"username": 123})
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == 40001
    assert payload["detail"]["errors"]


def test_unhandled_exception_maps_to_50005_without_leak():
    test_app = FastAPI()
    install_exception_handlers(test_app)

    @test_app.get("/boom")
    async def boom():
        raise RuntimeError("secret-token-kaboom")

    response = TestClient(test_app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500
    payload = response.json()
    assert payload["code"] == 50005
    assert payload["message"]
    assert "secret-token-kaboom" not in payload["message"]
    assert set(payload) == {"code", "message"}


# ---------- 40101 会话无效与会话归属越权 ----------


def test_refresh_without_session_returns_40101(plans_env):
    response = client.post(
        "/api/v1/plans/5/refresh",
        json={"trigger": "user_change", "changes": {"budget": "150"}},
    )
    assert response.status_code == 401
    payload = response.json()
    assert payload["code"] == 40101
    assert set(payload) == {"code", "message"}


def test_refresh_with_foreign_session_returns_40401(plans_env):
    create_conversation("token-1")
    response = client.post(
        "/api/v1/plans/5/refresh",
        json={
            "conversation_id": "1",
            "trigger": "user_change",
            "changes": {"budget": "150"},
        },
        cookies={"pczy_session": "token-2"},
    )
    assert response.status_code == 404
    payload = response.json()
    assert payload["code"] == 40401
    assert payload["session_id"] == "1"


def test_confirm_with_foreign_session_returns_40401(plans_env):
    create_conversation("token-1")
    FakeItinRepo.itineraries.append(
        SimpleNamespace(
            id=5,
            conversation_id=1,
            plan_type="省钱",
            status="草稿",
            total_cost=188,
            total_minutes=540,
            request_snapshot={},
            solver_note=None,
            data_time=None,
            version_lineage=1,
        )
    )
    response = client.post(
        "/api/v1/plans/5/confirm?conversation_id=1",
        cookies={"pczy_session": "token-2"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == 40401
    assert FakeItinRepo.itineraries[0].status == "草稿"


# ---------- 行程状态机 API 层 ----------


def test_confirm_replaced_plan_returns_40901(plans_env):
    create_conversation()
    FakeItinRepo.itineraries.append(
        SimpleNamespace(
            id=5,
            conversation_id=1,
            plan_type="省钱",
            status="已被替换",
            total_cost=188,
            total_minutes=540,
            request_snapshot={},
            solver_note=None,
            data_time=None,
            version_lineage=1,
        )
    )
    response = client.post(
        "/api/v1/plans/5/confirm?conversation_id=1",
        cookies={"pczy_session": "token-1"},
    )
    assert response.status_code == 409
    payload = response.json()
    assert payload["code"] == 40901
    assert "已被替换" in payload["message"]
    assert FakeItinRepo.itineraries[0].status == "已被替换"


def test_confirm_pending_suggestion_end_to_end(plans_env):
    create_conversation()
    FakeItinRepo.itineraries.append(
        SimpleNamespace(
            id=5,
            conversation_id=1,
            plan_type="省钱",
            status="挂起建议",
            total_cost=150,
            total_minutes=540,
            request_snapshot={"visit_date": "2026-10-21"},
            solver_note=None,
            data_time=None,
            version_lineage=2,
        )
    )
    FakeItemRepo.by_itinerary[5] = []

    response = client.post(
        "/api/v1/plans/5/confirm?conversation_id=1",
        cookies={"pczy_session": "token-1"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["plan_id"] == "5"
    assert payload["status"] == "confirmed"
    assert payload["plan_detail"]["status"] == "已确认"
    assert FakeItinRepo.itineraries[0].status == "已确认"
    assert FakeEventRepo.events[-1]["payload"]["action"] == "confirm"
    assert FakeEventRepo.events[-1]["payload"]["from_status"] == "挂起建议"


# ---------- 知识上传/编辑审计 ----------


async def auditing_ingest_document(
    session, *, title, topic, content, source, operator_id
):
    versions = [d.version for d in FakeKnowledgeRepo.documents if d.title == title]
    doc = FakeKnowledgeRepo.add_document(
        SimpleNamespace(
            title=title,
            topic=topic,
            version=max(versions) + 1 if versions else 1,
            status="有效",
            source=source,
            content=content,
            ingested_at=datetime(2026, 10, 20, 12, 0),
        )
    )
    await FakeAuditRepo(session).append(
        operator_id=operator_id,
        action="ingest",
        entity_type="knowledge_document",
        entity_id=doc.id,
        after_value={"title": title, "topic": topic, "version": doc.version},
    )
    return doc


def test_knowledge_upload_and_edit_produce_ingest_audit(admin_env):
    admin_env.monkeypatch.setattr(
        "app.rag.ingestion.ingest_document", auditing_ingest_document
    )
    headers = login(client)

    upload = client.post(
        "/api/v1/admin/knowledge",
        json={
            "title": "户部山古建筑群指南",
            "topic": "景点",
            "content": "户部山位于徐州市中心……",
            "source": "官方渠道",
        },
        headers=headers,
    )
    assert upload.status_code == 200
    assert upload.json()["version"] == 1

    edit = client.patch(
        "/api/v1/admin/knowledge/1",
        json={"content": "户部山更新后的介绍……"},
        headers=headers,
    )
    assert edit.status_code == 200
    assert edit.json()["version"] == 2

    ingest_logs = [l for l in FakeAuditRepo.logs if l.action == "ingest"]
    assert len(ingest_logs) == 2
    assert ingest_logs[0].operator_id == 1
    assert ingest_logs[0].entity_type == "knowledge_document"
    assert ingest_logs[0].after_value["version"] == 1
    assert ingest_logs[1].after_value["version"] == 2


# ---------- GET /admin/knowledge 列表 ----------


def make_knowledge_doc(title, topic, status, version=1):
    return SimpleNamespace(
        title=title,
        topic=topic,
        version=version,
        status=status,
        ingested_at=datetime(2026, 10, 19, 9, 0),
    )


def test_list_knowledge_filters_and_field_contract(admin_env):
    FakeKnowledgeRepo.add_document(make_knowledge_doc("云龙湖景区游览指南", "景点", "有效"))
    FakeKnowledgeRepo.add_document(make_knowledge_doc("徐州美食地图", "美食", "失效"))
    headers = login(client)

    response = client.get(
        "/api/v1/admin/knowledge", params={"topic": "景点"}, headers=headers
    )
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"items", "total"}
    assert payload["total"] == 1
    item = payload["items"][0]
    assert set(item) == {
        "document_id",
        "title",
        "topic",
        "version",
        "status",
        "ingested_at",
    }
    assert item["title"] == "云龙湖景区游览指南"
    assert item["topic"] == "景点"
    assert item["status"] == "有效"
    assert item["ingested_at"]

    by_status = client.get(
        "/api/v1/admin/knowledge", params={"status": "失效"}, headers=headers
    )
    assert by_status.status_code == 200
    assert by_status.json()["total"] == 1
    assert by_status.json()["items"][0]["title"] == "徐州美食地图"

    unfiltered = client.get("/api/v1/admin/knowledge", headers=headers)
    assert unfiltered.json()["total"] == 2


def test_list_knowledge_requires_admin_token(admin_env):
    response = client.get("/api/v1/admin/knowledge")
    assert response.status_code == 401
    assert response.json()["code"] == 40102


# ---------- GET /health ----------


@pytest.fixture()
def health_env(monkeypatch):
    state = {"db": True, "redis": True, "scheduler": True}

    async def fake_check_db():
        return state["db"]

    async def fake_ping_redis():
        return state["redis"]

    def fake_scheduler_status():
        return {"running": state["scheduler"], "jobs": ["price_monitor"]}

    monkeypatch.setattr("app.api.v1.health._check_db", fake_check_db)
    monkeypatch.setattr("app.api.v1.health.ping_redis", fake_ping_redis)
    monkeypatch.setattr("app.api.v1.health.scheduler_status", fake_scheduler_status)
    monkeypatch.setattr(
        "app.api.v1.health.metrics_snapshot", lambda: {"requests_total": 0}
    )
    return state


def test_health_all_ok_returns_200(health_env):
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["db"] is True
    assert payload["redis"] is True
    assert payload["scheduler"] is True
    assert payload["scheduler_jobs"] == ["price_monitor"]
    assert payload["metrics"] == {"requests_total": 0}


@pytest.mark.parametrize("component", ["db", "redis", "scheduler"])
def test_health_component_down_returns_503(health_env, component):
    health_env[component] = False
    response = client.get("/health")
    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload[component] is False


# ---------- POST /chat data_time ----------


def test_chat_plans_payload_carries_data_time(chat_env):
    runtime.set_graph(
        FakeGraph(
            {
                "final_response": AgentResponse(
                    response_type="plans",
                    content="已为您规划好三种方案",
                    plans=make_three_plans(),
                    data_time="2026-10-20T08:30:00+00:00",
                ),
            }
        )
    )

    response = client.post(
        "/api/v1/chat",
        json={"message": "预算300元一天，喜欢历史文化"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["data_time"] == "2026-10-20T08:30:00+00:00"
    assert len(payload["plans"]) == 3
    assert all(i.data_time is not None for i in FakeItinRepo.itineraries)


# ---------- OpenAPI 契约 ----------


def test_openapi_contract_core_routes_present():
    schema = app.openapi()
    paths = schema["paths"]
    expected = [
        "/health",
        "/api/v1/chat",
        "/api/v1/plans",
        "/api/v1/plans/{plan_id}",
        "/api/v1/plans/{plan_id}/confirm",
        "/api/v1/plans/{plan_id}/refresh",
        "/api/v1/admin/login",
        "/api/v1/admin/knowledge",
        "/api/v1/admin/audits",
    ]
    for path in expected:
        assert path in paths, f"OpenAPI 缺少路由 {path}"
    assert schema["info"]["title"]
    assert schema["info"]["version"]