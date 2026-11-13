"""14.7 性能压测基建：外部依赖延迟注入桩 + 指标统计 + 报告输出。

压测原则（离线可重复）：
- LLM / 高德路线 / 天气 / POI 检索 / 知识检索等外部 IO 以确定性延迟桩替代（延迟区间
  对齐 design/01 时间预算：LLM ≤3.5s、单条路线 ≤2s、检索 ≤1s），Redis 用进程内存实现替换。
- 优化引擎、候选生成、缓存读写、LLM 信号量背压、管道编排全部走真实代码。
- 因此 P90 断言验证的是"系统自身开销 + 等效真实外部延迟"下的端到端预算。

接线点说明（均按各调用方模块内的名字绑定替换）：
- LLM：app.clients.llm._post_chat（text_call/structured_call 模块内查找，全局生效）
- 路线/天气：app.tools.live.direction / app.tools.live.fetch_weather（get_route/get_weather 内部查找）
- POI 检索：app.agent.nodes.retrieve_pois.search_attractions
- 知识检索：app.agent.nodes.rag_qa.retrieve_xuzhou_knowledge
- 重规划：app.api.v1.plans.ReplanService（注入延迟化的 problem_builder，编排仍走真实代码）
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.core.metrics import record_llm_call
from app.tools.base import ToolResult
from app.tools.knowledge import KnowledgeRetrievalOutput, RetrievedChunkInfo
from app.tools.pois import Coordinate, PoiCandidate, TimeWindow

REPORT_PATH = Path(__file__).parent / "perf_report.md"

COUNTERS: dict[str, float] = {
    "llm_delay_s": 0.0,
    "amap_delay_s": 0.0,
    "poi_delay_s": 0.0,
    "rag_delay_s": 0.0,
    "replan_delay_s": 0.0,
    "llm_calls": 0,
    "amap_calls": 0,
    "weather_calls": 0,
    "poi_calls": 0,
    "rag_calls": 0,
    "replan_calls": 0,
    "redis_hits": 0,
    "redis_misses": 0,
}


def reset_counters() -> None:
    for key in COUNTERS:
        COUNTERS[key] = 0


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        if ":stale" not in key:
            if key in self.store:
                COUNTERS["redis_hits"] += 1
            else:
                COUNTERS["redis_misses"] += 1
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value

    async def delete(self, key: str):
        self.store.pop(key, None)


def install_fake_redis(monkeypatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr("app.cache.service.redis_client", fake)
    return fake


def install_llm_stub(
    monkeypatch,
    rng: random.Random,
    lo: float,
    hi: float,
    intent: str = "knowledge_qa",
) -> None:
    """替换 LLM HTTP 出口。按 system prompt 特征区分三类调用：
    - structured_call（含 "JSON Schema"）→ 返回可过 ParsedIntent 校验的 JSON（intent 可参数化）
    - RAG 问答（上下文含 "[1] 《" 引用块）→ 返回带合法引用且无阿拉伯数字的答案（一次通过引用校验）
    - explain（默认）→ 返回不含阿拉伯数字的说明文本（一次通过数字一致性校验）
    """

    async def fake_post_chat(messages: list[dict], temperature: float = 0.3) -> dict:
        delay = rng.uniform(lo, hi)
        await asyncio.sleep(delay)
        COUNTERS["llm_delay_s"] += delay
        COUNTERS["llm_calls"] += 1
        record_llm_call(is_error=False)
        system = messages[0]["content"] if messages else ""
        if "JSON Schema" in system:
            content = (
                '{"intent": "%s", "duration_hours": 8, "budget": 300, '
                '"interests": ["历史文化"]}' % intent
            )
        elif "[1]" in system and "《" in system:
            content = "龟山汉墓是西汉楚王刘注夫妇的合葬墓[1]，门票与开放时间以景区现场公示为准[2]。"
        else:
            content = (
                "已按预算与兴趣完成安排，三套方案均满足约束，"
                "重点景点已避开周一闭馆风险，午餐与交通均已嵌入行程。"
            )
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr("app.clients.llm._post_chat", fake_post_chat)


def install_amap_stub(monkeypatch, rng: random.Random, lo: float, hi: float) -> None:
    async def fake_direction(origin: str, dest: str, mode: str) -> dict:
        delay = rng.uniform(lo, hi)
        await asyncio.sleep(delay)
        COUNTERS["amap_delay_s"] += delay
        COUNTERS["amap_calls"] += 1
        minutes = {"步行": 28, "公交": 14, "驾车": 8}.get(mode, 15)
        return {
            "duration_minutes": minutes + (hash((origin, dest)) % 7),
            "distance_meters": 2600 + (hash((origin, dest, mode)) % 1800),
        }

    async def fake_fetch_weather(city_adcode: str = "320300") -> dict:
        delay = rng.uniform(lo, min(hi, lo + 0.2))
        await asyncio.sleep(delay)
        COUNTERS["amap_delay_s"] += delay
        COUNTERS["weather_calls"] += 1
        return {
            "condition": "多云",
            "temperature": "22℃",
            "precipitation_prob": "20%",
            "alert_level": None,
        }

    monkeypatch.setattr("app.tools.live.direction", fake_direction)
    monkeypatch.setattr("app.tools.live.fetch_weather", fake_fetch_weather)


def build_poi_pool(count: int = 30) -> list[PoiCandidate]:
    tags_pool = [
        ["历史", "汉文化"],
        ["自然", "湖泊"],
        ["美食", "街区"],
        ["文化", "博物馆"],
        ["历史", "街区"],
        ["自然", "公园"],
    ]
    pool: list[PoiCandidate] = []
    for i in range(1, count + 1):
        lng = 117.12 + (i % 7) * 0.025
        lat = 34.22 + (i % 5) * 0.028
        pool.append(
            PoiCandidate(
                poi_id=i,
                name=f"徐州景点{i}",
                category="景点",
                tags=tags_pool[i % len(tags_pool)],
                location=Coordinate(lng=round(lng, 6), lat=round(lat, 6)),
                visit_minutes=60 + (i % 6) * 15,
                ticket_price=(i % 4) * 20,
                interest_score=0.95 - (i % 10) * 0.02,
                indoor=(i % 3 == 0),
                open_window=TimeWindow(open_minute=540, close_minute=1080),
            )
        )
    return pool


def install_poi_stub(
    monkeypatch, rng: random.Random, pool: list[PoiCandidate], lo: float = 0.08, hi: float = 0.2
) -> None:
    async def fake_search_attractions(session, query) -> ToolResult:
        delay = rng.uniform(lo, hi)
        await asyncio.sleep(delay)
        COUNTERS["poi_delay_s"] += delay
        COUNTERS["poi_calls"] += 1
        return ToolResult[list[PoiCandidate]].success(list(pool))

    monkeypatch.setattr("app.agent.nodes.retrieve_pois.search_attractions", fake_search_attractions)


def install_knowledge_stub(
    monkeypatch, rng: random.Random, lo: float = 0.2, hi: float = 0.8, chunk_count: int = 3
) -> None:
    """知识检索桩：返回足够相似（高于阈值）的确定性 chunk，走正常问答路径。"""

    async def fake_retrieve(session, query) -> ToolResult:
        delay = rng.uniform(lo, hi)
        await asyncio.sleep(delay)
        COUNTERS["rag_delay_s"] += delay
        COUNTERS["rag_calls"] += 1
        chunks = [
            RetrievedChunkInfo(
                chunk_id=i,
                doc_title=f"徐州旅游知识{i}",
                topic="景点",
                source="官方公示",
                chunk_text=f"关于该问题的权威资料片段{i}，涵盖门票、开放时间与游览建议。",
                similarity=0.92 - i * 0.05,
            )
            for i in range(1, chunk_count + 1)
        ]
        return ToolResult[KnowledgeRetrievalOutput].success(
            KnowledgeRetrievalOutput(chunks=chunks)
        )

    monkeypatch.setattr("app.agent.nodes.rag_qa.retrieve_xuzhou_knowledge", fake_retrieve)


def install_rag_poi_repo_stub(monkeypatch, pool: list[PoiCandidate]) -> None:
    class FakePoiRepo:
        def __init__(self, session) -> None:
            pass

        async def list_active(self):
            return [SimpleNamespace(name=p.name) for p in pool]

    monkeypatch.setattr("app.agent.nodes.rag_qa.PoiRepository", FakePoiRepo)


async def fake_build_checkpointer():
    from langgraph.checkpoint.memory import InMemorySaver

    from app.agent.checkpointer import checkpoint_serializer

    return InMemorySaver(serde=checkpoint_serializer())


def make_perf_replan_service(rng: random.Random, pool: list[PoiCandidate], lo: float = 0.15, hi: float = 0.35):
    """返回 ReplanService 工厂：真实编排/引擎/落库，仅 problem 重建以确定性延迟桩替代 DB 全量查询。"""
    from app.agent.nodes.common import to_optimizer_candidate
    from app.optimizer.models import (
        Constraints,
        OptimizeProblem,
        RouteEdge,
        RouteOption,
    )
    from app.services.replan_service import ReplanService, RebuiltProblem

    candidates = [to_optimizer_candidate(p, 2) for p in pool]
    names = {p.poi_id: p.name for p in pool}
    edge = RouteEdge(
        walk=RouteOption(minutes=20, distance_meters=1500),
        transit=RouteOption(minutes=10, distance_meters=1500),
        drive=RouteOption(minutes=6, distance_meters=1500),
    )
    ids = [-1, *names.keys()]
    matrix = {(a, b): edge for a in ids for b in ids if a != b}

    async def builder(session, snapshot, must_include_ids):
        delay = rng.uniform(lo, hi)
        await asyncio.sleep(delay)
        COUNTERS["replan_delay_s"] += delay
        COUNTERS["replan_calls"] += 1
        constraints = Constraints(
            tour_date=date(2026, 10, 21),
            budget=float(snapshot.get("budget") or 300),
            hours=float(snapshot.get("duration_hours") or 8),
            origin_lat=34.257,
            origin_lng=117.184,
        )
        return RebuiltProblem(
            problem=OptimizeProblem(
                candidates=candidates, route_matrix=matrix, constraints=constraints
            ),
            names=names,
            degraded=False,
        )

    def factory(problem_builder=None, itinerary_service=None) -> ReplanService:
        return ReplanService(problem_builder=builder)

    return factory


def wrap_plans_stage_timers(monkeypatch) -> dict[str, list[float]]:
    """包装 plans 路由内六个真实节点工厂，记录分阶段耗时（ms），不改变任何节点行为。"""
    import app.api.v1.plans as plans_module

    stages: dict[str, list[float]] = {}

    factories = (
        "make_retrieve_pois_node",
        "make_fetch_live_data_node",
        "make_generate_candidates_node",
        "make_optimize_node",
        "make_validate_node",
        "make_explain_node",
    )

    for factory_name in factories:
        original = getattr(plans_module, factory_name)
        stage = factory_name.removeprefix("make_").removesuffix("_node")
        stages.setdefault(stage, [])

        def make_wrapper(original=original, stage=stage):
            def wrapper(*args, **kwargs):
                node = original(*args, **kwargs)

                async def timed(state):
                    start = time.perf_counter()
                    result = await node(state)
                    stages[stage].append((time.perf_counter() - start) * 1000)
                    return result

                return timed

            return wrapper

        monkeypatch.setattr(plans_module, factory_name, make_wrapper())

    return stages


def make_random_requests(n: int, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    interest_pool = ["历史文化", "自然风光", "美食", "博物馆", "汉文化", "街区"]
    requests: list[dict] = []
    for _ in range(n):
        interests = rng.sample(interest_pool, rng.randint(1, 3))
        requests.append(
            {
                "request": {
                    "duration_hours": rng.choice([4, 5, 6, 7, 8, 9, 10]),
                    "budget": rng.randint(150, 500),
                    "interests": interests,
                    "short_trip": rng.random() < 0.3,
                    "visit_date": "2026-10-21",
                }
            }
        )
    return requests


def make_questions(n: int, seed: int = 7) -> list[str]:
    rng = random.Random(seed)
    topics = ["龟山汉墓", "云龙湖", "汉兵马俑", "户部山古民居", "徐州博物馆", "彭祖园", "云龙山", "戏马台"]
    templates = [
        "{t}的开放时间是什么时候",
        "{t}门票多少钱",
        "{t}有什么游览建议",
        "{t}的历史背景是什么",
        "去{t}怎么坐车最方便",
    ]
    return [rng.choice(templates).format(t=rng.choice(topics)) for _ in range(n)]


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(ratio * (len(ordered) - 1))))
    return ordered[index]


async def run_concurrently(count: int, job, concurrency: int) -> list:
    semaphore = asyncio.Semaphore(concurrency)

    async def wrapped(index: int):
        async with semaphore:
            return await job(index)

    return await asyncio.gather(*(wrapped(i) for i in range(count)))


def stats_block(latencies_ms: list[float]) -> dict:
    return {
        "count": len(latencies_ms),
        "p50_ms": round(percentile(latencies_ms, 0.50)),
        "p90_ms": round(percentile(latencies_ms, 0.90)),
        "max_ms": round(max(latencies_ms)) if latencies_ms else 0,
    }


def stage_lines(stages: dict[str, list[float]]) -> list[str]:
    lines = []
    for name, values in stages.items():
        if not values:
            continue
        lines.append(
            f"阶段 {name}：调用 {len(values)} 次，"
            f"平均 {round(sum(values) / len(values))}ms / "
            f"P90 {round(percentile(values, 0.90))}ms / "
            f"最大 {round(max(values))}ms"
        )
    return lines


def counter_lines() -> list[str]:
    llm = COUNTERS["llm_calls"]
    amap = COUNTERS["amap_calls"]
    weather = COUNTERS["weather_calls"]
    poi = COUNTERS["poi_calls"]
    rag = COUNTERS["rag_calls"]
    replan = COUNTERS["replan_calls"]
    lines = [
        f"外部 IO 桩统计：LLM {llm} 次（累计 {round(COUNTERS['llm_delay_s'], 1)}s）、"
        f"路线 {amap} 次（{round(COUNTERS['amap_delay_s'], 1)}s）、天气 {weather} 次、"
        f"POI 检索 {poi} 次（{round(COUNTERS['poi_delay_s'], 1)}s）、"
        f"知识检索 {rag} 次（{round(COUNTERS['rag_delay_s'], 1)}s）、"
        f"重规划重建 {replan} 次（{round(COUNTERS['replan_delay_s'], 1)}s）",
        f"FakeRedis：命中 {int(COUNTERS['redis_hits'])} / 未命中 {int(COUNTERS['redis_misses'])}",
    ]
    return lines


def append_report(title: str, lines: list[str]) -> None:
    existing = ""
    if REPORT_PATH.exists():
        existing = REPORT_PATH.read_text(encoding="utf-8")
        existing = existing.replace("# 性能压测报告（自动生成）\n\n", "")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    block = ["# 性能压测报告（自动生成）", ""]
    if existing.strip():
        block.append(existing.strip())
        block.append("")
    block.append(f"## {title}")
    block.append("")
    block.append(f"- 运行时间：{stamp}")
    block.extend(f"- {line}" for line in lines)
    block.append("")
    REPORT_PATH.write_text("\n".join(block), encoding="utf-8")