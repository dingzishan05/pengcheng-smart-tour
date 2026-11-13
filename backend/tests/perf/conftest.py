"""14.7 压测统一环境：全部外部 IO 桩 + Fake 仓储 + 内存 Redis + 真实编排代码。

与 API 集成测试的差异：节点工厂、优化引擎、图编排、缓存读写、LLM 信号量全部真实，
仅替换外部 IO 出口与数据库仓储（内存实现），保证压测验证的是系统自身开销。
"""

from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from app.agent import runtime
from app.api.deps import get_session_factory
from app.core import metrics
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

SEED = 2026


@pytest.fixture()
def perf_env(monkeypatch):
    FakeConvRepo.reset()
    FakeMsgRepo.reset()
    FakeItinRepo.reset()
    FakeItemRepo.reset()
    FakeEventRepo.reset()
    stubs.reset_counters()
    metrics.reset_metrics()

    rng = random.Random(SEED)
    fake_redis = stubs.install_fake_redis(monkeypatch)
    pool = stubs.build_poi_pool(30)

    stubs.install_llm_stub(monkeypatch, rng, 1.0, 2.5, intent="knowledge_qa")
    stubs.install_amap_stub(monkeypatch, rng, 0.2, 0.8)
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

    monkeypatch.setattr(
        "app.api.v1.plans.ReplanService", stubs.make_perf_replan_service(rng, pool)
    )

    app.dependency_overrides[get_session_factory] = lambda: (lambda: FakeSessionCtx())
    monkeypatch.setattr("app.agent.runtime.SessionFactory", lambda: FakeSessionCtx())
    monkeypatch.setattr("app.agent.runtime.build_checkpointer", stubs.fake_build_checkpointer)

    stages = stubs.wrap_plans_stage_timers(monkeypatch)

    runtime.reset_graph()
    yield SimpleNamespace(stages=stages, pool=pool, redis=fake_redis, rng=rng)
    runtime.reset_graph()
    app.dependency_overrides.clear()