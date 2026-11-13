from __future__ import annotations

from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import patch

from app.tools import (
    get_opening_hours,
    get_ticket_price,
    retrieve_xuzhou_knowledge,
    search_attractions,
)
from app.tools.knowledge import KnowledgeQuery
from app.tools.pois import Coordinate, PoiQuery
from app.rag.retriever import RetrievalGate


def _poi(pid: int, *, name: str = "徐州博物馆", indoor: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id=pid,
        name=name,
        category="博物馆",
        tags=["汉文化", "博物馆"],
        latitude=34.265,
        longitude=117.195,
        visit_minutes=90,
        indoor=indoor,
        source_channel="运营录入",
        collected_at=datetime(2026, 10, 1, 9, 0),
    )


def _hours(*, closed: bool = False, valid_until: date | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        closed=closed,
        open_time=None if closed else time(9, 0),
        close_time=None if closed else time(17, 0),
        source_channel="官方渠道",
        collected_at=date(2026, 10, 1),
        valid_until=valid_until or date(2026, 12, 31),
    )


def _price(*, amount: float = 60.0, valid_until: date | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        amount=amount,
        source_channel="运营录入",
        collected_at=date(2026, 10, 1),
        valid_until=valid_until or date(2026, 12, 31),
    )


class FakeCache:
    def __init__(self, store: dict | None = None) -> None:
        self.store = store or {}
        self.set_calls: list[tuple[str, object]] = []

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value, ttl: int) -> None:
        self.set_calls.append((key, value))


class FakePoiRepo:
    def __init__(self, pois: list[SimpleNamespace]) -> None:
        self.pois = pois
        self.calls = 0

    async def list_active(self):
        self.calls += 1
        return self.pois


class FakeHoursRepo:
    def __init__(self, hours: SimpleNamespace | None) -> None:
        self.hours = hours

    async def get_hours(self, poi_id: int, visit_date: date):
        return self.hours


class FakePriceRepo:
    def __init__(self, price: SimpleNamespace | None) -> None:
        self.price = price
        self.calls = 0

    async def get_current(self, poi_id: int):
        self.calls += 1
        return self.price


def _patch_pois(poi_repo, hours_repo, price_repo, cache):
    return [
        patch("app.tools.pois.PoiRepository", return_value=poi_repo),
        patch("app.tools.pois.PoiHoursRepository", return_value=hours_repo),
        patch("app.tools.pois.PriceRepository", return_value=price_repo),
        patch("app.tools.pois.cache_get", side_effect=cache.get),
        patch("app.tools.pois.cache_set", side_effect=cache.set),
    ]


class _MultiPatch:
    def __init__(self, patches) -> None:
        self.patches = patches

    def __enter__(self):
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *args):
        for p in self.patches:
            p.stop()


async def test_search_attractions_success_filters_closed_and_budget():
    poi_repo = FakePoiRepo([_poi(1), _poi(2, name="闭馆景点"), _poi(3, name="高价景点")])
    hours_map = {1: _hours(), 2: _hours(closed=True), 3: _hours()}
    price_map = {1: _price(amount=0.0), 3: _price(amount=200.0)}

    class HoursRepo:
        async def get_hours(self, poi_id: int, visit_date: date):
            return hours_map.get(poi_id)

    class PriceRepo:
        async def get_current(self, poi_id: int):
            return price_map.get(poi_id)

    cache = FakeCache()
    with _MultiPatch(_patch_pois(poi_repo, HoursRepo(), PriceRepo(), cache)):
        result = await search_attractions(None, PoiQuery(day_of_week=2, budget_cap=100))
    assert result.ok
    names = [c.name for c in result.data]
    assert names == ["徐州博物馆"]
    assert result.data[0].ticket_price == 0.0
    assert result.meta.source_channel == "local_db"
    assert cache.set_calls  # 主动缓存写入


async def test_search_attractions_cache_hit_skips_repo():
    poi_repo = FakePoiRepo([_poi(1)])
    cache = FakeCache(
        store={
            "poi:active": [
                {
                    "poi_id": 9,
                    "name": "缓存景点",
                    "category": "景点",
                    "tags": ["自然风光"],
                    "lat": 34.2,
                    "lng": 117.2,
                    "visit_minutes": 60,
                    "indoor": False,
                    "source_channel": "运营录入",
                    "collected_at": None,
                }
            ]
        }
    )
    with _MultiPatch(_patch_pois(poi_repo, FakeHoursRepo(None), FakePriceRepo(None), cache)):
        result = await search_attractions(None, PoiQuery(day_of_week=3))
    assert result.ok
    assert result.data[0].name == "缓存景点"
    assert poi_repo.calls == 0


async def test_search_attractions_external_failure():
    class BrokenRepo:
        async def list_active(self):
            raise RuntimeError("db down")

    cache = FakeCache()
    with _MultiPatch(_patch_pois(BrokenRepo(), FakeHoursRepo(None), FakePriceRepo(None), cache)):
        result = await search_attractions(None, PoiQuery(day_of_week=1))
    assert not result.ok
    assert result.error.code == "50005"


async def test_get_opening_hours_success():
    with patch("app.tools.pois.PoiHoursRepository", return_value=FakeHoursRepo(_hours())):
        result = await get_opening_hours(None, 1, date(2026, 10, 21))
    assert result.ok
    assert result.data.windows[0].open_minute == 540
    assert result.data.closed_today is False
    assert result.data.is_reference is False


async def test_get_opening_hours_degrades_when_missing():
    with patch("app.tools.pois.PoiHoursRepository", return_value=FakeHoursRepo(None)):
        result = await get_opening_hours(None, 1, date(2026, 10, 21))
    assert result.ok
    assert result.data.is_reference is True
    assert result.data.closed_today is True
    assert result.meta.degraded is True


async def test_get_opening_hours_external_failure():
    class BrokenRepo:
        async def get_hours(self, poi_id: int, visit_date: date):
            raise RuntimeError("db down")

    with patch("app.tools.pois.PoiHoursRepository", return_value=BrokenRepo()):
        result = await get_opening_hours(None, 1, date(2026, 10, 21))
    assert not result.ok
    assert result.error.code == "50005"


async def test_get_ticket_price_success_fresh():
    price_repo = FakePriceRepo(_price(amount=60.0))
    cache = FakeCache()
    with _MultiPatch(_patch_pois(FakePoiRepo([]), FakeHoursRepo(None), price_repo, cache)):
        result = await get_ticket_price(None, 1)
    assert result.ok
    assert result.data.amount == 60.0
    assert result.data.freshness == "fresh"
    assert result.meta.degraded is False
    assert price_repo.calls == 1


async def test_get_ticket_price_cache_hit_skips_repo():
    price_repo = FakePriceRepo(_price())
    cache = FakeCache(
        store={
            "price:1": {
                "amount": 30.0,
                "source_channel": "运营录入",
                "collected_at": None,
                "valid_until": None,
                "freshness": "fresh",
                "is_reference": False,
            }
        }
    )
    with _MultiPatch(_patch_pois(FakePoiRepo([]), FakeHoursRepo(None), price_repo, cache)):
        result = await get_ticket_price(None, 1)
    assert result.ok
    assert result.data.amount == 30.0
    assert price_repo.calls == 0


async def test_get_ticket_price_unavailable_when_no_record():
    cache = FakeCache()
    with _MultiPatch(_patch_pois(FakePoiRepo([]), FakeHoursRepo(None), FakePriceRepo(None), cache)):
        result = await get_ticket_price(None, 1)
    assert not result.ok
    assert result.error.code == "40401"


async def test_get_ticket_price_degrades_to_stale_reference():
    stale_date = date(2026, 1, 1)
    price_repo = FakePriceRepo(_price(amount=60.0, valid_until=stale_date))
    cache = FakeCache()
    with _MultiPatch(_patch_pois(FakePoiRepo([]), FakeHoursRepo(None), price_repo, cache)):
        with patch("app.tools.pois.date") as fake_date:
            fake_date.today.return_value = date(2026, 10, 20)
            result = await get_ticket_price(None, 1)
    assert result.ok
    assert result.data.freshness == "stale"
    assert result.data.is_reference is True
    assert result.meta.degraded is True


async def test_retrieve_knowledge_success():
    chunk = SimpleNamespace(
        chunk_id=11,
        chunk_text="云龙湖位于徐州市区南部",
        similarity=0.87,
        metadata={"doc_title": "云龙湖指南", "topic": "景点介绍", "source": "官方", "version": 2},
    )
    fake_result = SimpleNamespace(
        chunks=[chunk],
        low_confidence=False,
        gate=RetrievalGate.WEAK,
        degraded=False,
        expanded_query="",
    )

    async def fake_retrieve(session, question, topic=None, top_k=8):
        return fake_result

    with patch("app.tools.knowledge.retrieve", side_effect=fake_retrieve):
        result = await retrieve_xuzhou_knowledge(None, KnowledgeQuery(question="云龙湖在哪"))
    assert result.ok
    assert result.data.chunks[0].doc_title == "云龙湖指南"
    assert result.data.chunks[0].doc_version == 2
    assert result.data.insufficient is False
    assert result.data.degraded is False
    assert result.meta.source_channel == "pgvector"


async def test_retrieve_knowledge_degrades_to_keyword_channel():
    fake_result = SimpleNamespace(
        chunks=[],
        low_confidence=True,
        gate=RetrievalGate.INSUFFICIENT,
        degraded=True,
        expanded_query="扩展查询",
    )

    async def fake_retrieve(session, question, topic=None, top_k=8):
        return fake_result

    with patch("app.tools.knowledge.retrieve", side_effect=fake_retrieve):
        result = await retrieve_xuzhou_knowledge(None, KnowledgeQuery(question="汉文化"))
    assert result.ok
    assert result.data.degraded is True
    assert result.data.insufficient is True
    assert result.data.low_confidence is True
    assert result.meta.source_channel == "keyword_fallback"
    assert result.meta.degraded is True


async def test_retrieve_knowledge_external_failure():
    async def broken_retrieve(session, question, topic=None, top_k=8):
        raise RuntimeError("vector db down")

    with patch("app.tools.knowledge.retrieve", side_effect=broken_retrieve):
        result = await retrieve_xuzhou_knowledge(None, KnowledgeQuery(question="汉文化"))
    assert not result.ok
    assert result.error.code == "50002"