from __future__ import annotations

import asyncio
import sys
from datetime import date, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionFactory
from app.core.logging import get_logger
from app.models import Poi, PoiHours, PriceRecord

logger = get_logger("seed.pois")

COLLECTED = date(2026, 10, 19)
VALID_UNTIL = COLLECTED + timedelta(days=settings.data_validity_days_official)
SOURCE_CHANNEL = "官方渠道"
SOURCE_PRIORITY_OFFICIAL = 1

MUSEUM_HOURS = {"closed_dow": {0}, "open": "09:00", "close": "17:00"}
PARK_HOURS = {"closed_dow": set(), "open": "06:00", "close": "22:00"}
MALL_HOURS = {"closed_dow": set(), "open": "10:00", "close": "22:00"}
FOOD_HOURS = {"closed_dow": set(), "open": "10:30", "close": "21:30"}
TRANSPORT_HOURS = {"closed_dow": set(), "open": "06:00", "close": "23:00"}

POIS: list[dict] = [
    {
        "name": "徐州博物馆",
        "category": "博物馆",
        "tags": ["汉文化", "历史"],
        "longitude": 117.1844,
        "latitude": 34.2572,
        "visit_minutes": 90,
        "indoor": True,
        "description": "国家一级博物馆，藏有金缕玉衣等两汉珍品，是了解徐州两汉文化的首选之地。",
        "hours": MUSEUM_HOURS,
        "ticket": 0,
    },
    {
        "name": "龟山汉墓",
        "category": "景点",
        "tags": ["汉文化", "历史"],
        "longitude": 117.1572,
        "latitude": 34.3044,
        "visit_minutes": 75,
        "indoor": True,
        "description": "西汉楚王刘注夫妇墓，两条甬道精度极高，被称为'千古奇观'。",
        "hours": MUSEUM_HOURS,
        "ticket": 60,
    },
    {
        "name": "汉文化景区",
        "category": "景点",
        "tags": ["汉文化", "历史"],
        "longitude": 117.2437,
        "latitude": 34.2271,
        "visit_minutes": 180,
        "indoor": False,
        "description": "含狮子山楚王陵、汉兵马俑博物馆、水下兵马俑馆，两汉文化核心景区。",
        "hours": MUSEUM_HOURS,
        "ticket": 90,
    },
    {
        "name": "户部山古民居",
        "category": "景点",
        "tags": ["历史", "民俗"],
        "longitude": 117.1913,
        "latitude": 34.2529,
        "visit_minutes": 90,
        "indoor": False,
        "description": "明清古民居建筑群，徐州'千年文脉'所在地，余翟郑三大家院落保存完好。",
        "hours": MUSEUM_HOURS,
        "ticket": 30,
    },
    {
        "name": "回龙窝历史街区",
        "category": "商圈",
        "tags": ["历史", "美食", "休闲"],
        "longitude": 117.1937,
        "latitude": 34.2577,
        "visit_minutes": 90,
        "indoor": False,
        "description": "清代建筑遗存街区，如今汇集文创小店与徐州特色餐饮，适合傍晚漫步。",
        "hours": MALL_HOURS,
        "ticket": 0,
    },
    {
        "name": "云龙湖",
        "category": "景点",
        "tags": ["自然风光", "休闲"],
        "longitude": 117.1511,
        "latitude": 34.2364,
        "visit_minutes": 150,
        "indoor": False,
        "description": "国家5A级云龙湖风景区，湖东路十里杏花、水族馆与音乐厅环湖分布。",
        "hours": PARK_HOURS,
        "ticket": 0,
    },
    {
        "name": "云龙山",
        "category": "景点",
        "tags": ["自然风光", "历史"],
        "longitude": 117.1731,
        "latitude": 34.2378,
        "visit_minutes": 120,
        "indoor": False,
        "description": "苏北名山，兴化寺、放鹤亭等古迹众多，山顶可俯瞰徐州全城与云龙湖。",
        "hours": PARK_HOURS,
        "ticket": 0,
    },
    {
        "name": "彭祖园",
        "category": "景点",
        "tags": ["自然风光", "休闲"],
        "longitude": 117.1708,
        "latitude": 34.2527,
        "visit_minutes": 90,
        "indoor": False,
        "description": "纪念彭祖的综合性公园，福山寿山相依，樱花季尤为出名。",
        "hours": PARK_HOURS,
        "ticket": 0,
    },
    {
        "name": "汉画像石艺术馆",
        "category": "博物馆",
        "tags": ["汉文化", "历史"],
        "longitude": 117.1590,
        "latitude": 34.2417,
        "visit_minutes": 60,
        "indoor": True,
        "description": "收藏汉画像石专题馆，位于云龙湖东岸，车马出行图等石刻精美绝伦。",
        "hours": MUSEUM_HOURS,
        "ticket": 0,
    },
    {
        "name": "淮海战役烈士纪念塔园林",
        "category": "景点",
        "tags": ["历史", "红色文化"],
        "longitude": 117.1345,
        "latitude": 34.2195,
        "visit_minutes": 100,
        "indoor": False,
        "description": "全国重点烈士纪念建筑，纪念塔、总前委群雕与纪念馆承载淮海战役历史。",
        "hours": MUSEUM_HOURS,
        "ticket": 0,
    },
    {
        "name": "泉山森林公园",
        "category": "景点",
        "tags": ["自然风光", "休闲"],
        "longitude": 117.1388,
        "latitude": 34.2125,
        "visit_minutes": 120,
        "indoor": False,
        "description": "五峰相连的城市森林公园，森林覆盖率高，适合徒步亲近自然。",
        "hours": PARK_HOURS,
        "ticket": 0,
    },
    {
        "name": "大龙湖风景区",
        "category": "景点",
        "tags": ["自然风光", "休闲"],
        "longitude": 117.2652,
        "latitude": 34.1951,
        "visit_minutes": 100,
        "indoor": False,
        "description": "新城区人工湖景区，环湖步道与湿地景观，周边为现代化城市界面。",
        "hours": PARK_HOURS,
        "ticket": 0,
    },
    {
        "name": "徐州乐园",
        "category": "景点",
        "tags": ["休闲", "亲子"],
        "longitude": 117.1267,
        "latitude": 34.1962,
        "visit_minutes": 240,
        "indoor": False,
        "description": "大型主题乐园，含加勒比水世界与游乐区，适合亲子与年轻人游玩。",
        "hours": MALL_HOURS,
        "ticket": 199,
    },
    {
        "name": "户部山步行街",
        "category": "商圈",
        "tags": ["美食", "休闲", "购物"],
        "longitude": 117.1905,
        "latitude": 34.2545,
        "visit_minutes": 60,
        "indoor": False,
        "description": "户部山下的时尚步行街，餐饮购物一体，紧邻古民居景区。",
        "hours": MALL_HOURS,
        "ticket": 0,
    },
    {
        "name": "苏宁广场",
        "category": "商圈",
        "tags": ["购物", "美食"],
        "longitude": 117.1858,
        "latitude": 34.2594,
        "visit_minutes": 90,
        "indoor": True,
        "description": "徐州地标商圈，淮海经济区最大商业综合体之一。",
        "hours": MALL_HOURS,
        "ticket": 0,
    },
    {
        "name": "彭城壹号",
        "category": "商圈",
        "tags": ["美食", "休闲"],
        "longitude": 117.1822,
        "latitude": 34.2610,
        "visit_minutes": 60,
        "indoor": False,
        "description": "市中心文化休闲街区，保留老建筑肌理，汇集餐饮与文创。",
        "hours": MALL_HOURS,
        "ticket": 0,
    },
    {
        "name": "富国街美食街",
        "category": "餐饮",
        "tags": ["美食"],
        "longitude": 117.1798,
        "latitude": 34.2624,
        "visit_minutes": 60,
        "indoor": False,
        "description": "徐州人熟悉的市井美食街，地锅鸡、烧烤、米线老店集中。",
        "hours": FOOD_HOURS,
        "ticket": 0,
    },
    {
        "name": "滨湖餐饮区",
        "category": "餐饮",
        "tags": ["美食"],
        "longitude": 117.1560,
        "latitude": 34.2403,
        "visit_minutes": 60,
        "indoor": False,
        "description": "云龙湖北岸餐饮聚集区，湖景餐厅为主，可品尝徐州特色菜。",
        "hours": FOOD_HOURS,
        "ticket": 0,
    },
    {
        "name": "徐州东站",
        "category": "交通枢纽",
        "tags": ["交通"],
        "longitude": 117.3221,
        "latitude": 34.2180,
        "visit_minutes": 15,
        "indoor": True,
        "description": "京沪高铁徐州东站，多数外地游客的到达站。",
        "hours": TRANSPORT_HOURS,
        "ticket": 0,
    },
    {
        "name": "徐州站",
        "category": "交通枢纽",
        "tags": ["交通"],
        "longitude": 117.2046,
        "latitude": 34.2656,
        "visit_minutes": 15,
        "indoor": True,
        "description": "徐州火车站（普速），市中心位置，地铁1号线直达。",
        "hours": TRANSPORT_HOURS,
        "ticket": 0,
    },
]


def _parse_time(value: str) -> time:
    h, m = value.split(":")
    return time(hour=int(h), minute=int(m))


async def seed_pois(session: AsyncSession) -> dict:
    stats = {"pois_created": 0, "pois_skipped": 0, "hours_created": 0, "prices_created": 0}
    for item in POIS:
        stmt = select(Poi).where(Poi.name == item["name"])
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            stats["pois_skipped"] += 1
            continue
        poi = Poi(
            name=item["name"],
            category=item["category"],
            tags=item["tags"],
            longitude=item["longitude"],
            latitude=item["latitude"],
            visit_minutes=item["visit_minutes"],
            indoor=item["indoor"],
            description=item["description"],
            status="有效",
            source_channel=SOURCE_CHANNEL,
            collected_at=COLLECTED,
        )
        session.add(poi)
        await session.flush()
        stats["pois_created"] += 1

        hours = item["hours"]
        for dow in range(7):
            closed = dow in hours["closed_dow"]
            session.add(
                PoiHours(
                    poi_id=poi.id,
                    day_of_week=dow,
                    open_time=None if closed else _parse_time(hours["open"]),
                    close_time=None if closed else _parse_time(hours["close"]),
                    closed=closed,
                    source_channel=SOURCE_CHANNEL,
                    collected_at=COLLECTED,
                    valid_until=VALID_UNTIL,
                )
            )
            stats["hours_created"] += 1

        session.add(
            PriceRecord(
                poi_id=poi.id,
                price_type="门票",
                amount=item["ticket"],
                source_channel=SOURCE_CHANNEL,
                source_priority=SOURCE_PRIORITY_OFFICIAL,
                collected_at=COLLECTED,
                valid_until=VALID_UNTIL,
                review_status="已通过",
                is_current=True,
            )
        )
        stats["prices_created"] += 1
    await session.commit()
    logger.info(
        "seed pois done",
        extra={"kind": "data", **stats},
    )
    return stats


async def run() -> None:
    async with SessionFactory() as session:
        await seed_pois(session)


if __name__ == "__main__":
    asyncio.run(run())