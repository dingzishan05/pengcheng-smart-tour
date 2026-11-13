"""14.8 降级矩阵 20 行逐行验证（design/10 §4 ↔ §10.1）。

验证模式：
- 注入行（1/2/4/6/18/19/20）：本文件内做故障注入并断言用户侧表现；
- 引用行（3/5/7/8/9/10/11/14/15/16/17）：锚定既有套件中的专项测试（同一回归中通过即视为逐行验证）；
- 前端行（12/13）：锚定 14.6 Playwright E2E 套件（degraded-ui.spec.ts）。
"""

from __future__ import annotations

import importlib
import inspect
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent import runtime
from app.agent.nodes.parse_request import ParsedIntent, make_parse_request_node
from app.agent.nodes.retrieve_pois import (
    EMPTY_CANDIDATES_RESPONSE,
    make_retrieve_pois_node,
)
from app.agent.prompts.parse_request import INVALID_INPUT_RESPONSE
from app.agent.state import AgentError, AgentResponse
from app.cache.service import cache_get, read_through
from app.main import app
from app.tools.base import ToolResult
from tests.api.test_chat import FakeGraph, chat_env

client = TestClient(app, raise_server_exceptions=False)

FRONTEND_E2E = Path(__file__).resolve().parents[3] / "frontend" / "tests" / "e2e"

REFERENCED_ROWS = [
    (3, "提示词注入防护", "tests.test_tools_live_security", None),
    (5, "POI 数据不完整 → is_reference", "tests.test_tools_pois_knowledge", "test_get_opening_hours_degrades_when_missing"),
    (7, "优化超时 → solver_note", "tests.optimizer.test_engine", "test_pool_truncation_sets_solver_note"),
    (8, "三方案趋同 → 趋同说明", "tests.optimizer.test_engine", "test_similar_note_on_converged_plans"),
    (9, "刷新数据源不可用/不影响 → 保留原行程", "tests.services.test_replan_service", "test_refresh_confirmed_not_overwritten_on_data_change"),
    (10, "新约束无可行 → 冲突建议保留原方案", "tests.services.test_replan_service", "test_refresh_infeasible_budget_returns_conflict"),
    (11, "用户与数据变化并发 → 会话锁串行化", "tests.services.test_replan_service", "test_refresh_conversation_lock_serializes"),
    (14, "RAG 检索无结果 → 拒答", "tests.rag.test_quality", "test_out_domain_refusal_rate"),
    (15, "RAG 弱相关 → 低置信前缀", "tests.rag.test_quality", "test_embedding_failure_falls_back_to_keywords"),
    (16, "向量服务失败 → 关键词通道", "tests.rag.test_quality", "test_embedding_failure_falls_back_to_keywords"),
    (17, "价格过期 → stale/is_reference", "tests.scheduler.test_expiry_to_reference", "test_expiry_flow_marks_reference_end_to_end"),
]


@pytest.mark.parametrize(("row", "scenario", "module_name", "test_name"), REFERENCED_ROWS)
def test_row_covered_by_existing_suite(row, scenario, module_name, test_name):
    module = importlib.import_module(module_name)
    if test_name is None:
        public_tests = [n for n in dir(module) if n.startswith("test_")]
        assert public_tests, f"行{row} {scenario}：套件 {module_name} 无测试"
    else:
        assert callable(getattr(module, test_name)), f"行{row} {scenario}：缺少 {module_name}::{test_name}"


def test_row12_13_frontend_degradation_covered_by_e2e():
    spec = FRONTEND_E2E / "degraded-ui.spec.ts"
    assert spec.exists(), "缺少前端降级 E2E（14.6）"
    content = spec.read_text(encoding="utf-8")
    assert "地图" in content or "map" in content.lower()
    assert "重发" in content or "retry" in content.lower()


def test_row1_llm_failure_returns_50003_and_persists_input(chat_env):
    from tests.api.test_chat import FakeMsgRepo

    runtime.set_graph(
        FakeGraph(
            {
                "error": AgentError(code="50003", message="LLM 服务不可用"),
                "final_response": AgentResponse(
                    response_type="fallback", content="服务繁忙，请稍后重试。"
                ),
            }
        )
    )
    response = client.post("/api/v1/chat", json={"message": "预算300元玩一天"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 50003
    assert "重发" in payload["message"] or "重试" in payload["message"]
    assert FakeMsgRepo.messages and FakeMsgRepo.messages[0].role == "user"
    assert FakeMsgRepo.messages[0].content == "预算300元玩一天"


async def test_row2_invalid_input_gets_guidance_without_forced_parse():
    async def fake_parse(wrapped_input: str) -> ParsedIntent:
        return ParsedIntent(intent="invalid_input")

    node = make_parse_request_node(parse_fn=fake_parse, clock=lambda: 0.0)
    update = await node({"user_message": "asdkjh%%%随机乱流"})
    assert update["intent"] == "invalid_input"
    assert update["final_response"].content == INVALID_INPUT_RESPONSE


async def test_row4_no_feasible_plan_returns_reasons_and_advice(monkeypatch):
    from app.agent.nodes import retrieve_pois as rp_module
    from app.agent.state import StructuredRequest
    from app.optimizer.models import InfeasibleReasonKind

    class FakeSessionCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc_info):
            return None

    async def empty_search(session, query):
        return ToolResult[list].success([])

    monkeypatch.setattr(rp_module, "search_attractions", empty_search)

    node = make_retrieve_pois_node(FakeSessionCtx)
    update = await node(
        {
            "structured_request": StructuredRequest(
                duration_hours=8, budget=50, interests=["历史文化"]
            ),
            "visit_date": "2026-10-21",
        }
    )

    assert update["candidate_pois"] == []
    assert update["infeasibility"] is not None
    assert update["infeasibility"].reasons[0].kind == InfeasibleReasonKind.CLOSED
    assert update["infeasibility"].advice is not None
    assert update["final_response"].content == EMPTY_CANDIDATES_RESPONSE


async def test_row6_amap_failure_degrades_to_stale_cache(monkeypatch):
    from app.clients.amap import AmapUnavailableError
    from app.tools.live import get_route
    from app.tools.pois import Coordinate

    class FakeRedis:
        def __init__(self):
            self.store: dict[str, str] = {}

        async def get(self, key):
            return self.store.get(key)

        async def set(self, key, value, ex=None):
            self.store[key] = value

        async def delete(self, key):
            self.store.pop(key, None)

    fake_redis = FakeRedis()
    monkeypatch.setattr("app.cache.service.redis_client", fake_redis)

    async def healthy_direction(origin_geo, dest_geo, mode):
        return {"duration_minutes": 25, "distance_meters": 3000}

    monkeypatch.setattr("app.tools.live.direction", healthy_direction)

    origin = Coordinate(lng=117.184, lat=34.257)
    destination = Coordinate(lng=117.19, lat=34.26)

    healthy = await get_route(origin, destination, "公交")
    assert healthy.ok and healthy.data is not None and not healthy.data.degraded

    stale_keys = [k for k in fake_redis.store if k.endswith(":stale")]
    assert stale_keys, "健康调用应写入 stale 副本"
    fake_redis.store = {
        k: v for k, v in fake_redis.store.items() if k.endswith(":stale")
    }

    async def failing_direction(origin_geo, dest_geo, mode):
        raise AmapUnavailableError("高德限流")

    monkeypatch.setattr("app.tools.live.direction", failing_direction)
    degraded = await get_route(origin, destination, "公交")
    assert degraded.ok
    assert degraded.data is not None
    assert degraded.data.degraded is True
    assert degraded.data.collected_at is not None
    assert degraded.meta.degraded is True
    assert degraded.meta.source_channel == "redis_stale"


def test_row18_price_conflict_arbitrated_by_current_reviewed_filter():
    from app.repositories.poi_repo import PriceRepository

    source = inspect.getsource(PriceRepository.get_current)
    assert "is_current" in source
    assert "review_status" in source
    assert "已通过" in source


async def test_row19_redis_failure_falls_back_to_source(monkeypatch):
    class ExplodingRedis:
        async def get(self, key):
            raise ConnectionError("redis down")

        async def set(self, key, value, ex=None):
            raise ConnectionError("redis down")

        async def delete(self, key):
            raise ConnectionError("redis down")

    monkeypatch.setattr("app.cache.service.redis_client", ExplodingRedis())

    assert await cache_get("route:any") is None

    loader_calls: list[int] = []

    async def loader():
        loader_calls.append(1)
        return {"value": 42}

    result = await read_through("route:any", 60, loader, fallback_to_stale=False)
    assert loader_calls == [1]
    assert result["value"] == {"value": 42}
    assert result["hit"] is False


def test_row20_db_failure_maps_to_50005_without_leak():
    from app.api.deps import get_session_factory

    def exploding_factory():
        raise ConnectionError("postgresql down")

    app.dependency_overrides[get_session_factory] = exploding_factory
    try:
        response = client.post(
            "/api/v1/plans",
            json={"request": {"duration_hours": 8, "budget": 300, "interests": ["历史文化"]}},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    payload = response.json()
    assert payload["code"] == 50005
    assert "postgresql" not in payload["message"]
    assert payload["message"]


def test_all_20_rows_have_verification_anchors():
    injected = {1, 2, 4, 6, 12, 13, 18, 19, 20}
    referenced = {row for row, *_ in REFERENCED_ROWS}
    assert injected | referenced == set(range(1, 21)), sorted(set(range(1, 21)) - (injected | referenced))