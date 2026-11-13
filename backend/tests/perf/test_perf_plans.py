"""14.7 性能压测：DFX 4.1 全部指标落地验证（pytest -m perf 单独运行）。

- 4.1.2 行程生成 P90 ≤30s：100 次随机需求（10 并发），七阶段编排全真实
- 4.1.4 行程刷新 P90 ≤15s：user_change / data_change 混合 30 次
- 4.1.1 知识问答 P90 ≤5s：50 个问题走真实 TourGraph（parse→rag_qa→generate）
- 4.1.5 ≥50 用户并发在线：50 用户各执行 行程生成+问答+查看行程 混合会话，零失败无死等
- 路线缓存命中率 >95%：预热后按 metrics.cache_hit_rate 断言
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from httpx import ASGITransport

from app.core import metrics
from app.main import app
from tests.perf import stubs

pytestmark = pytest.mark.perf

API = "/api/v1"
stubs.REPORT_PATH.unlink(missing_ok=True)


def make_client(cookie: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://perf-test",
        cookies={"pczy_session": cookie},
        timeout=60.0,
    )


async def timed_post(client: httpx.AsyncClient, path: str, payload: dict):
    start = time.perf_counter()
    response = await client.post(path, json=payload)
    return response, (time.perf_counter() - start) * 1000


async def timed_get(client: httpx.AsyncClient, path: str):
    start = time.perf_counter()
    response = await client.get(path)
    return response, (time.perf_counter() - start) * 1000


async def test_perf_plan_generation_p90(perf_env):
    requests = stubs.make_random_requests(100, seed=42)

    async with make_client("perf-plan-warm") as client:
        response, warm_ms = await timed_post(client, f"{API}/plans", requests[0])
        assert response.status_code == 200, response.text
        assert len(response.json()["plans"]) == 3

    async def job(index: int):
        async with make_client(f"perf-plan-{index}") as client:
            response, elapsed_ms = await timed_post(client, f"{API}/plans", requests[index])
            body = response.json() if response.status_code == 200 else None
            return response.status_code, body, elapsed_ms

    outcomes = await stubs.run_concurrently(100, job, concurrency=10)

    failures = [(code, body) for code, body, _ in outcomes if code != 200]
    assert not failures, failures[:3]
    latencies = [ms for _, _, ms in outcomes]
    assert {len(body["plans"]) for _, body, _ in outcomes} == {3}

    p90 = stubs.percentile(latencies, 0.90)
    snapshot = metrics.snapshot()

    assert p90 <= 30_000, f"行程生成 P90 {p90:.0f}ms 超出 DFX 4.1.2 预算 30s"
    assert snapshot["cache_hit_rate"] > 0.95, snapshot

    stubs.append_report(
        "4.1.2 行程生成压测（100 次随机需求，并发 10，预热 1 次）",
        [
            f"冷启动首请求（预热，不计入 P90）：{round(warm_ms)}ms",
            f"端到端延迟：{stubs.stats_block(latencies)}",
            f"指标快照：{snapshot}",
            *stubs.counter_lines(),
            "分阶段耗时分解：",
            *stubs.stage_lines(perf_env.stages),
        ],
    )


async def test_perf_refresh_p90(perf_env):
    seed_request = stubs.make_random_requests(1, seed=99)[0]
    async with make_client("perf-refresh-0") as client:
        response, _ = await timed_post(client, f"{API}/plans", seed_request)
        assert response.status_code == 200, response.text
        payload = response.json()
        conversation_id = payload["conversation_id"]
        plan_id = payload["plans"][0]["plan_id"]

    refresh_jobs = []
    for i in range(30):
        if i % 3 == 2:
            changes = {"heavy_rain": True}
            trigger = "data_change"
        else:
            changes = {"budget": (250, 200, 280, 220)[i % 4]}
            trigger = "user_change"
        refresh_jobs.append(
            {
                "conversation_id": conversation_id,
                "trigger": trigger,
                "changes": changes,
            }
        )

    async def job(index: int):
        async with make_client("perf-refresh-0") as client:
            response, elapsed_ms = await timed_post(
                client, f"{API}/plans/{plan_id}/refresh", refresh_jobs[index]
            )
            body = response.json() if response.status_code == 200 else None
            return response.status_code, body, elapsed_ms

    outcomes = await stubs.run_concurrently(30, job, concurrency=8)

    failures = [(code, body) for code, body, _ in outcomes if code != 200]
    assert not failures, failures[:3]
    latencies = [ms for _, _, ms in outcomes]
    effective = [
        body for _, body, _ in outcomes if body and (body.get("new_plan") or body.get("conflict"))
    ]
    assert len(effective) == 30

    p90 = stubs.percentile(latencies, 0.90)
    assert p90 <= 15_000, f"行程刷新 P90 {p90:.0f}ms 超出 DFX 4.1.4 预算 15s"

    stubs.append_report(
        "4.1.4 行程刷新压测（30 次 user_change/data_change 混合，并发 8）",
        [
            f"端到端延迟：{stubs.stats_block(latencies)}",
            f"有效结果（新方案或冲突建议）：{len(effective)}/30",
            *stubs.counter_lines(),
        ],
    )


async def test_perf_qa_p90(perf_env, monkeypatch):
    stubs.install_llm_stub(monkeypatch, perf_env.rng, 0.6, 1.6, intent="knowledge_qa")
    questions = stubs.make_questions(50, seed=7)

    async with make_client("perf-qa-warm") as client:
        response, _ = await timed_post(client, f"{API}/chat", {"message": "徐州有什么好玩的地方"})
        assert response.status_code == 200, response.text
        assert response.json()["response_type"] == "knowledge_answer"

    async def job(index: int):
        async with make_client(f"perf-qa-{index}") as client:
            response, elapsed_ms = await timed_post(
                client, f"{API}/chat", {"message": questions[index % len(questions)]}
            )
            body = response.json() if response.status_code == 200 else {}
            return response.status_code, body.get("response_type"), elapsed_ms

    outcomes = await stubs.run_concurrently(50, job, concurrency=10)

    failures = [
        (code, kind) for code, kind, _ in outcomes if code != 200 or kind != "knowledge_answer"
    ]
    assert not failures, failures[:3]
    latencies = [ms for _, _, ms in outcomes]

    p90 = stubs.percentile(latencies, 0.90)
    assert p90 <= 5_000, f"知识问答 P90 {p90:.0f}ms 超出 DFX 4.1.1 预算 5s"

    stubs.append_report(
        "4.1.1 知识问答压测（50 个问题，并发 10，走真实 TourGraph）",
        [
            f"端到端延迟：{stubs.stats_block(latencies)}",
            f"指标快照：{metrics.snapshot()}",
            *stubs.counter_lines(),
        ],
    )


async def test_perf_50_concurrent_users(perf_env, monkeypatch):
    stubs.install_llm_stub(monkeypatch, perf_env.rng, 0.6, 1.6, intent="knowledge_qa")
    requests = stubs.make_random_requests(50, seed=11)
    questions = stubs.make_questions(50, seed=13)

    async with make_client("perf-mix-warm") as client:
        response, _ = await timed_post(client, f"{API}/plans", requests[0])
        assert response.status_code == 200, response.text

    async def user_session(user_id: int):
        records: list[tuple[str, float]] = []
        errors: list[tuple[str, int]] = []
        async with make_client(f"perf-user-{user_id}") as client:
            response, ms = await timed_post(client, f"{API}/plans", requests[user_id])
            records.append(("plans", ms))
            if response.status_code != 200:
                errors.append(("plans", response.status_code))
                return records, errors
            payload = response.json()
            plan_id = payload["plans"][0]["plan_id"]
            conversation_id = payload["conversation_id"]

            for round_id in range(2):
                chat_response, chat_ms = await timed_post(
                    client,
                    f"{API}/chat",
                    {"conversation_id": conversation_id, "message": questions[user_id]},
                )
                records.append(("chat", chat_ms))
                if chat_response.status_code != 200:
                    errors.append((f"chat-{round_id}", chat_response.status_code))

                detail_response, detail_ms = await timed_get(
                    client, f"{API}/plans/{plan_id}?conversation_id={conversation_id}"
                )
                records.append(("get_plan", detail_ms))
                if detail_response.status_code != 200:
                    errors.append((f"get_plan-{round_id}", detail_response.status_code))
        return records, errors

    start = time.perf_counter()
    outcomes = await asyncio.gather(*(user_session(user_id) for user_id in range(50)))
    wall_seconds = time.perf_counter() - start

    total_errors = [err for _, errors in outcomes for err in errors]
    assert not total_errors, total_errors[:5]

    all_records = [rec for records, _ in outcomes for rec in records]
    by_kind: dict[str, list[float]] = {}
    for kind, ms in all_records:
        by_kind.setdefault(kind, []).append(ms)
    max_request_ms = max(ms for _, ms in all_records)

    assert max_request_ms < 30_000, f"单请求 {max_request_ms:.0f}ms，存在排队死等风险"
    assert len(all_records) == 250

    stubs.append_report(
        "4.1.5 五十用户并发在线（每人：行程生成 1 + 问答 2 + 查看行程 2，共 250 请求）",
        [
            f"总墙钟：{round(wall_seconds)}s，吞吐 {round(len(all_records) / wall_seconds, 1)} req/s",
            f"单请求最大延迟：{round(max_request_ms)}ms（<30s 无死等）",
            *[
                f"{kind}：{stubs.stats_block(values)}"
                for kind, values in by_kind.items()
            ],
            f"指标快照：{metrics.snapshot()}",
            *stubs.counter_lines(),
        ],
    )