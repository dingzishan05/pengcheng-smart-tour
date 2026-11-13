from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import Poi
from tests.api.test_chat import FakeConvRepo, FakeSessionCtx

client = TestClient(app)


def make_poi(poi_id=1, name="龟山汉墓", status="有效"):
    return Poi(
        id=poi_id,
        name=name,
        category="景点",
        tags=["历史", "汉文化"],
        longitude=117.15,
        latitude=34.29,
        city="徐州市",
        visit_minutes=90,
        indoor=False,
        status=status,
        description="西汉楚襄王刘注夫妇合葬墓",
        source_channel="官方渠道",
        collected_at=date(2026, 9, 1),
    )


def make_price(poi_id=1, amount=60, channel="官方渠道", current=True, status="已通过"):
    return SimpleNamespace(
        id=11,
        poi_id=poi_id,
        price_type="门票",
        amount=amount,
        source_channel=channel,
        source_priority=1,
        collected_at=date(2026, 10, 1),
        valid_until=date(2026, 12, 30),
        review_status=status,
        is_current=current,
        reviewed_by=None,
        conflict_note=None,
    )


class FakePoiRepo:
    pois: list[Poi] = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.pois = []

    async def get(self, poi_id):
        return next((p for p in self.pois if p.id == poi_id), None)

    async def list_paged(self, category=None, tag=None, status="有效", page=1, page_size=20):
        rows = []
        for p in self.pois:
            if status is not None and p.status != status:
                continue
            if category is not None and p.category != category:
                continue
            if tag is not None and tag not in p.tags:
                continue
            rows.append(p)
        total = len(rows)
        start = (page - 1) * page_size
        return rows[start : start + page_size], total


class FakePriceRepo:
    records: list = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.records = []

    async def get_current(self, poi_id, price_type="门票"):
        return next(
            (
                r
                for r in self.records
                if r.poi_id == poi_id
                and r.price_type == price_type
                and r.is_current
                and r.review_status == "已通过"
            ),
            None,
        )

    async def list_history(self, poi_id, price_type=None):
        rows = [r for r in self.records if r.poi_id == poi_id]
        if price_type is not None:
            rows = [r for r in rows if r.price_type == price_type]
        return sorted(rows, key=lambda r: r.id, reverse=True)


class FakeHoursRepo:
    def __init__(self, session) -> None:
        pass

    async def list_week(self, poi_id):
        return [
            SimpleNamespace(day_of_week=d, open_time=None if d == 1 else __import__("datetime").time(9, 0), close_time=None if d == 1 else __import__("datetime").time(17, 0), closed=(d == 1))
            for d in range(7)
        ]


class FakeDataSourceRepo:
    def __init__(self, session) -> None:
        pass

    async def list_all(self):
        return [
            SimpleNamespace(name="高德路线", kind="external", last_success_at=datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc), availability="可用", last_error=None),
            SimpleNamespace(name="天气服务", kind="external", last_success_at=None, availability="不可用", last_error="timeout"),
        ]


class FakeMessageRepo:
    messages: list = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.messages = []

    async def count(self, conversation_id):
        return len([m for m in self.messages if m.conversation_id == conversation_id])

    async def list_history(self, conversation_id, limit=50, offset=0):
        rows = [m for m in self.messages if m.conversation_id == conversation_id]
        return rows[offset : offset + limit]


class FakePendingItinRepo:
    pending: dict = {}

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.pending = {}

    async def count_pending_by_conversations(self, conversation_ids):
        return {cid: self.pending.get(cid, 0) for cid in conversation_ids}


@pytest.fixture()
def data_env(monkeypatch):
    FakePoiRepo.reset()
    FakePriceRepo.reset()
    FakeMessageRepo.reset()
    FakeConvRepo.reset()
    FakePendingItinRepo.reset()

    from app.api.deps import get_session_factory

    app.dependency_overrides[get_session_factory] = lambda: (lambda: FakeSessionCtx())
    monkeypatch.setattr("app.api.v1.data.PoiRepository", FakePoiRepo)
    monkeypatch.setattr("app.api.v1.data.PriceRepository", FakePriceRepo)
    monkeypatch.setattr("app.api.v1.data.PoiHoursRepository", FakeHoursRepo)
    monkeypatch.setattr("app.api.v1.data.DataSourceRepository", FakeDataSourceRepo)
    monkeypatch.setattr("app.api.v1.conversations.MessageRepository", FakeMessageRepo)
    monkeypatch.setattr("app.api.v1.conversations.ConversationRepository", FakeConvRepo)
    monkeypatch.setattr(
        "app.api.v1.conversations.ItineraryRepository", FakePendingItinRepo
    )
    monkeypatch.setattr("app.api.deps.ConversationRepository", FakeConvRepo)
    yield monkeypatch
    app.dependency_overrides.clear()


def test_list_pois_contract(data_env):
    FakePoiRepo.pois = [make_poi(1, "龟山汉墓"), make_poi(2, "汉文化景区"), make_poi(3, "失效景点", status="失效")]
    FakePriceRepo.records = [make_price(1, amount=60)]

    response = client.get("/api/v1/pois?page=1&page_size=2")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"items", "total", "page", "page_size"}
    assert payload["total"] == 2
    assert payload["page"] == 1
    assert payload["page_size"] == 2
    assert len(payload["items"]) == 2
    item = payload["items"][0]
    assert item["poi_id"] == "1"
    assert item["ticket_price"] == "60.00"
    assert item["price_meta"]["source_channel"] == "官方渠道"
    assert item["price_meta"]["is_reference"] is False
    assert item["location"]["lat"] == 34.29


def test_list_pois_filters(data_env):
    FakePoiRepo.pois = [make_poi(1, "龟山汉墓"), make_poi(2, "博物馆A"), make_poi(3, "关闭景点", status="失效")]
    FakePoiRepo.pois[1].category = "博物馆"
    response = client.get("/api/v1/pois", params={"category": "博物馆"})
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["name"] == "博物馆A"

    tag_response = client.get("/api/v1/pois", params={"tag": "汉文化"})
    assert tag_response.json()["total"] == 2

    inactive = client.get("/api/v1/pois", params={"active": "false"})
    assert inactive.json()["total"] == 3


def test_get_poi_detail_with_hours_and_freshness(data_env):
    FakePoiRepo.pois = [make_poi(1)]
    FakePriceRepo.records = [make_price(1, amount=60)]
    FakePriceRepo.records[0].valid_until = date(2026, 8, 1)

    response = client.get("/api/v1/pois/1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "龟山汉墓"
    assert payload["status"] == "有效"
    assert len(payload["hours"]) == 7
    closed_monday = [h for h in payload["hours"] if h["day_of_week"] == 1]
    assert closed_monday[0]["closed"] is True
    assert payload["price_meta"]["is_reference"] is True
    assert payload["ticket_price"] == "60.00"


def test_get_poi_not_found(data_env):
    response = client.get("/api/v1/pois/999")
    assert response.status_code == 404
    assert response.json()["code"] == 40401


def test_routes_endpoint_explicit_unavailable(data_env, monkeypatch):
    from app.tools.base import ToolMeta, ToolResult
    from app.tools.live import RouteOptionInfo

    async def fake_get_route(origin, destination, mode):
        if mode == "公交":
            return ToolResult.success(
                RouteOptionInfo(mode=mode, duration_minutes=35, distance_meters=7800, collected_at=datetime.now(timezone.utc)),
                ToolMeta(source_channel="高德"),
            )
        if mode == "驾车":
            return ToolResult.success(
                RouteOptionInfo(mode=mode, duration_minutes=18, distance_meters=6200, collected_at=datetime.now(timezone.utc), degraded=True),
                ToolMeta(source_channel="高德", degraded=True),
            )
        return ToolResult.success(
            RouteOptionInfo(mode=mode, unavailable=True, reason="暂无步行数据"),
            ToolMeta(source_channel="amap"),
        )

    monkeypatch.setattr("app.api.v1.data.get_route", fake_get_route)
    FakePoiRepo.pois = [make_poi(1)]

    response = client.get(
        "/api/v1/routes",
        params={"origin": "117.2010,34.2643", "destination": "1", "modes": "公交,驾车,步行"},
    )
    assert response.status_code == 200
    routes = response.json()["routes"]
    by_mode = {r["mode"]: r for r in routes}
    assert by_mode["公交"]["duration_minutes"] == 35
    assert "unavailable" not in by_mode["公交"]
    assert by_mode["驾车"]["cost_meta"]["is_reference"] is True
    assert by_mode["步行"]["unavailable"] is True
    assert by_mode["步行"]["reason"] == "暂无步行数据"
    assert "duration_minutes" not in by_mode["步行"]


def test_routes_invalid_coordinate(data_env):
    response = client.get(
        "/api/v1/routes",
        params={"origin": "abc", "destination": "117.2,34.25"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == 40001


def test_prices_visitor_hides_hidden_records(data_env):
    current = make_price(1, amount=60, status="已通过")
    pending = make_price(1, amount=70, channel="游客上报", current=False, status="待审核")
    rejected = make_price(1, amount=80, channel="游客上报", current=False, status="已驳回")
    pending.id, rejected.id = 12, 13
    FakePriceRepo.records = [current, pending, rejected]

    response = client.get("/api/v1/prices/1")
    payload = response.json()
    assert payload["current"]["amount"] == "60.00"
    assert len(payload["records"]) == 1

    full = client.get("/api/v1/prices/1", params={"include_hidden": "true"})
    assert len(full.json()["records"]) == 3


def test_sources_status(data_env):
    response = client.get("/api/v1/sources/status")
    payload = response.json()
    names = {s["name"] for s in payload["sources"]}
    assert names == {"高德路线", "天气服务"}
    unavailable = [s for s in payload["sources"] if s["name"] == "天气服务"]
    assert unavailable[0]["availability"] == "不可用"


def test_conversations_list_and_messages(data_env):
    conv = SimpleNamespace(
        id=1,
        session_token="token-9",
        title="徐州一日游",
        status="active",
        last_active_at=datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc),
    )
    FakeConvRepo.conversations.append(conv)
    FakeMessageRepo.messages = [
        SimpleNamespace(id=1, conversation_id=1, role="user", content="你好", response_type=None, plan_refs=None, sent_at=datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)),
        SimpleNamespace(id=2, conversation_id=1, role="system", content="请问预算？", response_type="clarify", plan_refs=None, sent_at=datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)),
    ]

    listing = client.get("/api/v1/conversations", cookies={"pczy_session": "token-9"})
    payload = listing.json()
    assert payload["total"] == 1
    assert payload["items"][0]["title"] == "徐州一日游"

    messages = client.get(
        "/api/v1/conversations/1/messages",
        params={"page": 1, "page_size": 1},
        cookies={"pczy_session": "token-9"},
    )
    mpayload = messages.json()
    assert mpayload["total"] == 2
    assert len(mpayload["items"]) == 1
    assert mpayload["page_size"] == 1

    page2 = client.get(
        "/api/v1/conversations/1/messages",
        params={"page": 2, "page_size": 1},
        cookies={"pczy_session": "token-9"},
    )
    assert page2.json()["items"][0]["role"] == "system"
    assert page2.json()["items"][0]["response_type"] == "clarify"


def test_conversation_messages_rejects_foreign_token(data_env):
    conv = SimpleNamespace(
        id=1, session_token="token-9", title=None, status="active", last_active_at=None
    )
    FakeConvRepo.conversations.append(conv)
    response = client.get(
        "/api/v1/conversations/1/messages",
        cookies={"pczy_session": "other-token"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == 40401

def test_delete_conversation_soft_deletes_and_hides(data_env):
    conv = SimpleNamespace(
        id=1,
        session_token="token-9",
        title="徐州一日游",
        status="active",
        deleted_at=None,
        last_active_at=None,
    )
    FakeConvRepo.conversations.append(conv)

    deleted = client.delete(
        "/api/v1/conversations/1",
        cookies={"pczy_session": "token-9"},
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"conversation_id": "1", "status": "deleted"}
    assert conv.status == "deleted"
    assert conv.deleted_at is not None


def test_delete_conversation_foreign_token_404(data_env):
    conv = SimpleNamespace(
        id=2,
        session_token="token-other",
        title="他人会话",
        status="active",
        deleted_at=None,
        last_active_at=None,
    )
    FakeConvRepo.conversations.append(conv)

    response = client.delete(
        "/api/v1/conversations/2",
        cookies={"pczy_session": "token-9"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == 40401
    assert conv.status == "active"
