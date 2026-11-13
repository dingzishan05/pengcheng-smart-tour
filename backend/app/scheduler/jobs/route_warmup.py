from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.models import TransportRoute
from app.repositories.poi_repo import PoiRepository, TransportRouteRepository
from app.tools.live import get_route
from app.tools.pois import Coordinate

logger = get_logger("app.scheduler.route_warmup")

MODES = ("公交", "步行", "驾车")
DEFAULT_QPS = 3


async def route_matrix_warmup(session_factory=SessionFactory, qps: int = DEFAULT_QPS) -> dict:
    async with session_factory() as session:
        pois = await PoiRepository(session).list_active()
    if len(pois) < 2:
        return {"pairs": 0, "saved": 0, "unavailable": 0, "skipped": True}

    semaphore = asyncio.Semaphore(max(1, qps))
    combos: list[tuple[object, object, str]] = [
        (a, b, mode) for a in pois for b in pois if a.id != b.id for mode in MODES
    ]

    async def fetch(a, b, mode):
        async with semaphore:
            return await get_route(
                Coordinate(lng=float(a.longitude), lat=float(a.latitude)),
                Coordinate(lng=float(b.longitude), lat=float(b.latitude)),
                mode,
            )

    results = await asyncio.gather(*(fetch(a, b, m) for a, b, m in combos))

    saved = 0
    unavailable = 0
    routes: list[TransportRoute] = []
    for (a, b, mode), result in zip(combos, results):
        if not result.ok or result.data is None or result.data.unavailable:
            unavailable += 1
            continue
        routes.append(
            TransportRoute(
                origin_poi_id=a.id,
                dest_poi_id=b.id,
                mode=mode,
                duration_minutes=result.data.duration_minutes,
                distance_meters=result.data.distance_meters,
                source_channel="高德",
                collected_at=result.data.collected_at or datetime.now(timezone.utc),
            )
        )
        saved += 1

    if routes:
        async with session_factory() as session:
            route_repo = TransportRouteRepository(session)
            for route in routes:
                await route_repo.upsert_route(route)
            await session.commit()

    stats = {
        "pairs": len(combos),
        "saved": saved,
        "unavailable": unavailable,
        "skipped": False,
    }
    logger.info("route_matrix_warmup done", extra={"kind": "system", **stats})
    return stats
