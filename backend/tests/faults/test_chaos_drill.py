"""14.8 混沌演练（design/10 §10.5）：LLM 超时 / Redis 宕机 / 地图限流三场景各一次。

演示前夜执行：pytest tests/faults/test_chaos_drill.py -q

与降级矩阵注入测试的差异：本文件在真实编排（TourGraph 全图 + 真实缓存/
熔断/降级代码 + 真实 HTTP 请求链路）之上注入单点故障，验证系统级表现：
- 行1（LLM 超时）：chat 返回 50003 fallback 话术且输入落库；行程生成降级模板解释；
- 行19（Redis 宕机）：行程生成功能无损（性能降级不功能降级），无死等；
- 行6（地图限流）：行程仍生成，data_time 携带降级痕迹（DFX 4.2.5 降级有痕）。
"""

from __future__ import annotations

import random
import time
from types import SimpleNamespace

import httpx
import pytest
from httpx import ASGITransport

from app.agent import runtime
from app.api.deps import get_session_factory
from app.clients.circuit_breaker import llm_breaker
from app.main import app
from tests.api.test_chat import (
    FakeConvRepo,
    FakeEventRepo,
    FakeItinRepo,
    FakeItemRepo,
    FakeMsgRepo,
    FakeSessionCtx,
)
from tests.perf import stubs

API = "/api/v1"
SEED = 2026

PLAN_REQUEST = {
    "request": {
        "duration_hours": 8,
        "budget": 300,
        "interests": ["历史文化"],
        "short_trip": False,
    }
}


def make_client(cookie: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://chaos-test",
        cookies={"pczy_session": cookie},
        timeout=45.0,
    )


@pytest.fixture()
def chaos_env(monkeypatch):
    FakeConvRepo.reset()
    FakeMsgRepo.reset()
    FakeItinRepo.reset()
    FakeItemRepo.reset()
    FakeEventRepo.reset()
    stubs.reset_counters()

    rng = random.Random(SEED)
    fake_redis = stubs.install_fake_redis(monkeypatch)
    pool = stubs.build_poi_pool(30)

    stubs.install_llm_stub(monkeypatch, rng, 0.0, 0.0)
    stubs.install_amap_stub(monkeypatch, rng, 0.0, 0.0)
    stubs.install_poi_stub(monkeypatch, rng, pool)
    stubs.install_knowledge_stub(monkeypatch, rng)
    stubs.install_rag_poi_repo_stub(monkeypatch, pool)

    for module, name, target in [
        ("app.api.v1.plans", "ItineraryRepository", FakeItinRepo),
        ("app.api.v1.plans", "ItineraryItemRepository", FakeItemRepo),
        ("app.api.v1.plans", "ChangeEventRepository", FakeEventRepo),
        ("app.api.v1.chat", "ConversationRepository", FakeConvRepo),
        ("app.api.v1.chat", "MessageRepository", FakeMsgRepo),
        ("app.api.v1.chat", "ItineraryRepository", FakeItinRepo),
        ("app.api.v1.chat", "ItineraryItemRepository", FakeItemRepo),
        ("app.api.v1.chat", "ChangeEventRepository", FakeEventRepo),
        ("app.api.deps", "ConversationRepository", FakeConvRepo),
    ]:
        monkeypatch.setattr(f"{module}.{name}", target)

    for name, fake in [
        ("ItineraryRepository", lambda session: FakeItinRepo(session)),
        ("ItineraryItemRepository", lambda session: FakeItemRepo(session)),
        ("ChangeEventRepository", lambda session: FakeEventRepo(session)),
    ]:
        monkeypatch.setattr(f"app.services.replan_service.{name}", fake)

    app.dependency_overrides[get_session_factory] = lambda: (lambda: FakeSessionCtx())
    monkeypatch.setattr("app.agent.runtime.SessionFactory", lambda: FakeSessionCtx())
    monkeypatch.setattr("app.agent.runtime.build_checkpointer", stubs.fake_build_checkpointer)

    llm_breaker.record_success()
    runtime.reset_graph()
    yield SimpleNamespace(pool=pool, rng=rng, redis=fake_redis)
    runtime.reset_graph()
    app.dependency_overrides.clear()


async def test_chaos_llm_timeout_keeps_service_usable(chaos_env, monkeypatch):
    from app.clients.llm import LLMUnavailableError

    async def timeout_post(messages, temperature=0.3):
        raise LLMUnavailableError("LLM 请求超时")

    monkeypatch.setattr("app.clients.llm._post_chat", timeout_post)

    user_message = "预算300元玩一天，想去汉文化景点"
    async with make_client("chaos-llm") as client:
        chat = await client.post(f"{API}/chat", json={"message": user_message})
        assert chat.status_code == 200
        chat_payload = chat.json()
        assert chat_payload["code"] == 50003
        assert "重试" in chat_payload["message"] or "重发" in chat_payload["message"]
        assert any(
            m.role == "user" and m.content == user_message for m in FakeMsgRepo.messages
        ), "本轮输入必须落库（降级矩阵行1）"

        plan = await client.post(f"{API}/plans", json=PLAN_REQUEST)
        assert plan.status_code == 200, plan.text
        payload = plan.json()
        assert len(payload["plans"]) == 3
        assert "为您规划了" in payload["plan_details"][0]["explanation"], (
            "LLM 超时后 explain 必须降级为模板话术，而非失败"
        )
        assert len(FakeItinRepo.itineraries) == 3


async def test_chaos_redis_down_keeps_functionality(chaos_env, monkeypatch):
    class ExplodingRedis:
        async def get(self, key):
            raise ConnectionError("redis down")

        async def set(self, key, value, ex=None):
            raise ConnectionError("redis down")

        async def delete(self, key):
            raise ConnectionError("redis down")

    monkeypatch.setattr("app.cache.service.redis_client", ExplodingRedis())

    async with make_client("chaos-redis") as client:
        start = time.perf_counter()
        response = await client.post(f"{API}/plans", json=PLAN_REQUEST)
        elapsed = time.perf_counter() - start

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["plans"]) == 3, "Redis 故障下行程生成功能不得降级（矩阵行19）"
    assert len(FakeItinRepo.itineraries) == 3
    assert elapsed < 35, f"Redis 故障下单请求 {elapsed:.1f}s，违反 DFX 4.2.1（禁止死等）"


async def test_chaos_amap_rate_limited_marks_degraded(chaos_env, monkeypatch):
    from app.clients.amap import AmapUnavailableError

    async with make_client("chaos-amap") as client:
        healthy = await client.post(f"{API}/plans", json=PLAN_REQUEST)
        assert healthy.status_code == 200
        assert healthy.json()["data_time"] is None

        stale_keys = [k for k in chaos_env.redis.store if k.endswith(":stale")]
        assert stale_keys, "健康调用应写入 stale 副本"
        chaos_env.redis.store = {
            k: v for k, v in chaos_env.redis.store.items() if k.endswith(":stale")
        }

        async def limited_direction(origin_geo, dest_geo, mode):
            raise AmapUnavailableError("高德限流")

        monkeypatch.setattr("app.tools.live.direction", limited_direction)

        start = time.perf_counter()
        degraded = await client.post(f"{API}/plans", json=PLAN_REQUEST)
        elapsed = time.perf_counter() - start

    assert degraded.status_code == 200, degraded.text
    payload = degraded.json()
    assert len(payload["plans"]) == 3, "地图限流下行程仍须生成（矩阵行6）"
    data_time = payload["data_time"]
    assert data_time is not None, "降级结果必须携带数据时间标注（DFX 4.2.5）"
    assert "降级" in data_time
    assert elapsed < 35, f"地图限流下单请求 {elapsed:.1f}s，违反 DFX 4.2.1（禁止死等）"