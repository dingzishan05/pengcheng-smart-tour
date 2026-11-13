from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta, timezone

from app.agent.state import (
    ORIGIN_KEY_ID,
    StructuredRequest,
    TourState,
    encode_route_key,
)
from app.cache.keys import route_key
from app.cache.service import cache_get
from app.core.config import settings
from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.core.metrics import record_cache_hit, record_cache_miss
from app.optimizer.models import (
    RouteEdge,
    RouteOption,
    TransportMode,
    WeatherInfo,
    haversine_km,
)
from app.repositories.poi_repo import TransportRouteRepository
from app.tools.live import RouteOptionInfo, WeatherSnapshot, get_route, get_weather
from app.tools.pois import Coordinate, PoiCandidate

logger = get_logger("app.agent.fetch_live")

MAX_ROUTE_POIS = 8
ROUTE_CONCURRENCY = 8
PER_CALL_TIMEOUT_SECONDS = 6.0
MODES = (TransportMode.WALK, TransportMode.TRANSIT, TransportMode.DRIVE)
CITY = "徐州"

# 超过该直线距离不再请求步行路线：降级前的高德步行耗时已远超可游玩范围，
# 既能省下调用量，也不会改变可行方案。
WALK_MAX_KM = 4.0

WEATHER_RAIN_KEYWORDS = ("暴雨", "大雨", "台风")
WEATHER_WIND_KEYWORDS = ("大风", "台风")


def _geo(point: Coordinate) -> str:
    return f"{point.lng:.6f},{point.lat:.6f}"


def _to_weather_info(snapshot: WeatherSnapshot) -> WeatherInfo:
    text = f"{snapshot.condition} {snapshot.alert_level or ''}"
    return WeatherInfo(
        condition_text=snapshot.condition,
        has_heavy_rain=any(w in text for w in WEATHER_RAIN_KEYWORDS),
        has_strong_wind=any(w in text for w in WEATHER_WIND_KEYWORDS),
    )


def _route_option(info: RouteOptionInfo | None) -> RouteOption | None:
    if info is None or info.unavailable or info.duration_minutes is None:
        return None
    return RouteOption(minutes=info.duration_minutes, distance_meters=info.distance_meters or 0)


def _select_route_pois(candidates: list[PoiCandidate], pinned: list[str]) -> list[PoiCandidate]:
    ordered = sorted(candidates, key=lambda c: -c.interest_score)
    picked: list[PoiCandidate] = []
    for c in ordered:
        if any(name in c.name or c.name in name for name in pinned):
            picked.append(c)
    for c in ordered:
        if c not in picked:
            picked.append(c)
        if len(picked) >= MAX_ROUTE_POIS:
            break
    return picked[:MAX_ROUTE_POIS]


async def _fetch_leg(
    origin: Coordinate,
    destination: Coordinate,
    mode: TransportMode,
    semaphore: asyncio.Semaphore,
) -> RouteOptionInfo | None:
    mode_value = mode.value
    cached = await cache_get(route_key(_geo(origin), _geo(destination), mode_value))
    if cached is not None:
        record_cache_hit()
        try:
            return RouteOptionInfo.model_validate(cached)
        except Exception:
            pass
    else:
        record_cache_miss()
    async with semaphore:
        try:
            result = await asyncio.wait_for(
                get_route(origin, destination, mode_value), timeout=PER_CALL_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            return RouteOptionInfo(mode=mode_value, unavailable=True, reason="查询超时")
    if result.ok and result.data is not None:
        return result.data
    return RouteOptionInfo(mode=mode_value, unavailable=True, reason="暂无数据")


def _db_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=settings.route_db_max_age_days)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def _load_db_routes(poi_ids: set[int]) -> dict[tuple[int, int, str], RouteOption]:
    """读取持久化的 POI↔POI 路线矩阵（由 route_matrix_warmup 定时预热）。

    读库失败只降级为“没有缓存”，不影响本次行程生成。
    """
    if not poi_ids:
        return {}
    try:
        async with SessionFactory() as session:
            rows = await TransportRouteRepository(session).list_all()
    except Exception as exc:
        logger.warning(
            "load transport_routes failed, fallback to live amap",
            extra={"kind": "external", "error": str(exc)[:200]},
        )
        return {}

    cutoff = _db_cutoff()
    routes: dict[tuple[int, int, str], RouteOption] = {}
    for row in rows:
        if row.duration_minutes is None or row.duration_minutes <= 0:
            continue
        if row.origin_poi_id not in poi_ids or row.dest_poi_id not in poi_ids:
            continue
        if row.collected_at is not None and _as_utc(row.collected_at) < cutoff:
            continue
        routes[(row.origin_poi_id, row.dest_poi_id, row.mode)] = RouteOption(
            minutes=row.duration_minutes,
            distance_meters=row.distance_meters or 0,
        )
    return routes


def make_fetch_live_data_node():
    async def fetch_live_data_node(state: TourState) -> dict:
        request: StructuredRequest = state["structured_request"]
        candidates: list[PoiCandidate] = list(state.get("candidate_pois") or [])
        visit_raw = state.get("visit_date")
        try:
            visit = date.fromisoformat(visit_raw) if visit_raw else date.today()
        except ValueError:
            visit = date.today()

        origin = (
            Coordinate(lat=request.origin.lat, lng=request.origin.lng)
            if request.origin
            else Coordinate(lat=34.2570, lng=117.1840)
        )
        selected = _select_route_pois(candidates, request.pinned_pois)
        poi_ids = {c.poi_id for c in selected}
        db_routes = await _load_db_routes(poi_ids)

        points: dict[int, Coordinate] = {ORIGIN_KEY_ID: origin}
        for cand in selected:
            points[cand.poi_id] = cand.location

        # 去程、回程都要：build_sequence_pool 用回程耗时做时间可行性剪枝。
        # 但回程只用于剪枝、不进入行程，因此与去程一起优先于景点间边。
        origin_specs: list[tuple[int, int, TransportMode]] = []
        return_specs: list[tuple[int, int, TransportMode]] = []
        pair_specs: list[tuple[int, int, TransportMode]] = []
        for cand in selected:
            distance = haversine_km(origin.lat, origin.lng, cand.location.lat, cand.location.lng)
            for mode in MODES:
                if mode is TransportMode.WALK and distance > WALK_MAX_KM and (
                    ORIGIN_KEY_ID,
                    cand.poi_id,
                    mode.value,
                ) not in db_routes:
                    continue
                origin_specs.append((ORIGIN_KEY_ID, cand.poi_id, mode))
                return_specs.append((cand.poi_id, ORIGIN_KEY_ID, mode))

        for a in selected:
            for b in selected:
                if a.poi_id == b.poi_id:
                    continue
                distance = haversine_km(
                    a.location.lat, a.location.lng, b.location.lat, b.location.lng
                )
                for mode in MODES:
                    if mode is TransportMode.WALK and distance > WALK_MAX_KM and (
                        a.poi_id,
                        b.poi_id,
                        mode.value,
                    ) not in db_routes:
                        continue
                    pair_specs.append((a.poi_id, b.poi_id, mode))

        # 起点边优先（决定可达性），其次按目标景点兴趣度排序，预算耗尽时优先保住关键边。
        interest = {c.poi_id: c.interest_score for c in selected}
        pair_specs.sort(key=lambda s: (-interest.get(s[1], 0), s[0], s[1]))
        specs = origin_specs + return_specs + pair_specs

        budget = max(0, int(settings.amap_call_budget))
        degraded = False
        planned: list[tuple[int, int, TransportMode]] = []
        edge_modes: dict[tuple[int, int], dict[str, RouteOption | None]] = {}
        for a, b, mode in specs:
            edge_modes.setdefault((a, b), {"walk": None, "transit": None, "drive": None})
            cached_route = db_routes.get((a, b, mode.value))
            if cached_route is not None:
                edge_modes[(a, b)][_mode_field(mode)] = cached_route
                continue
            if budget <= 0:
                degraded = True
                continue
            budget -= 1
            planned.append((a, b, mode))

        semaphore = asyncio.Semaphore(ROUTE_CONCURRENCY)
        tasks = [
            asyncio.create_task(_fetch_leg(points[a], points[b], mode, semaphore))
            for a, b, mode in planned
        ]
        # 图级 30s 总预算：为 explain 预留降级时间，超时未完成的边按“暂无数据”处理，
        # 避免地图服务变慢时把整次生成拖到不可用。
        deadline = state.get("budget_deadline")
        timeout: float | None = None
        if deadline is not None:
            timeout = deadline - time.monotonic() - float(settings.explain_fallback_reserve_seconds)
            timeout = max(0.5, timeout)
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=timeout)
            for task in pending:
                task.cancel()
            if pending:
                degraded = True
                await asyncio.gather(*pending, return_exceptions=True)
            results = [
                task.result() if task in done and not task.cancelled() else None
                for task in tasks
            ]
        else:
            results = []
        for (a, b, mode), info in zip(planned, results):
            # 个别两点之间没有公交/步行方案是高德常见结果，不等于本次行程数据降级；
            # 只有拿到的是过期缓存（degraded）才在这里标记。
            if info is not None and info.degraded:
                degraded = True
            edge_modes[(a, b)][_mode_field(mode)] = _route_option(info)

        matrix: dict[str, RouteEdge] = {
            encode_route_key(a, b): RouteEdge(**modes) for (a, b), modes in edge_modes.items()
        }

        # 真正的数据缺口：起点到某个候选景点完全没有可用交通方式（会影响可达性判断）。
        if any(
            matrix.get(encode_route_key(ORIGIN_KEY_ID, poi_id)) is None
            or not matrix[encode_route_key(ORIGIN_KEY_ID, poi_id)].available_modes()
            for poi_id in poi_ids
        ):
            degraded = True

        weather_result = await get_weather(CITY, visit)
        weather: WeatherInfo | None = None
        if weather_result.ok and weather_result.data is not None:
            weather = _to_weather_info(weather_result.data)
            if weather_result.meta.degraded or weather_result.data.degraded:
                degraded = True
        else:
            degraded = True

        logger.info(
            "fetch_live finished pois=%s edges=%s db_legs=%s amap_calls=%s degraded=%s",
            len(selected),
            len(matrix),
            len(db_routes),
            len(planned),
            degraded,
            extra={"kind": "system"},
        )
        return {
            "route_matrix": matrix,
            "weather": weather,
            "data_degraded": degraded,
        }

    fetch_live_data_node.__name__ = "fetch_live_data"
    return fetch_live_data_node


def _mode_field(mode: TransportMode) -> str:
    return {
        TransportMode.WALK: "walk",
        TransportMode.TRANSIT: "transit",
        TransportMode.DRIVE: "drive",
    }[mode]
