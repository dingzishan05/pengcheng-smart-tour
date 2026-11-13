from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from app.core.db import SessionFactory
from app.models import Poi, PoiHours, PriceRecord


async def verify() -> list[str]:
    problems: list[str] = []
    async with SessionFactory() as session:
        poi_count = (await session.execute(select(func.count(Poi.id)))).scalar_one()
        if not 15 <= poi_count <= 30:
            problems.append(f"POI count {poi_count} not in [15, 30]")

        hour_rows = (
            await session.execute(
                select(PoiHours.poi_id, func.count(PoiHours.id)).group_by(PoiHours.poi_id)
            )
        ).all()
        bad_hours = [poi_id for poi_id, cnt in hour_rows if cnt != 7]
        if bad_hours:
            problems.append(f"POIs without 7 hour rows: {bad_hours}")

        current_prices = (
            await session.execute(
                select(PriceRecord.poi_id, func.count(PriceRecord.id)).where(
                    PriceRecord.is_current.is_(True),
                    PriceRecord.review_status == "已通过",
                ).group_by(PriceRecord.poi_id)
            )
        ).all()
        multi = [rec for rec in current_prices if rec[1] > 1]
        if multi:
            problems.append(f"multiple current prices found: {multi}")

        pois_without_price = poi_count - len(current_prices)
        if pois_without_price > 0:
            problems.append(f"{pois_without_price} POIs have no current price")

        inactive_names = (
            await session.execute(select(Poi.name).where(Poi.status != "有效"))
        ).scalars().all()
        if inactive_names:
            problems.append(f"seed created inactive POIs unexpectedly: {inactive_names}")
    return problems


async def main() -> int:
    problems = await verify()
    if problems:
        for p in problems:
            print("PROBLEM:", p)
        return 1
    print("VERIFY SEED OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))