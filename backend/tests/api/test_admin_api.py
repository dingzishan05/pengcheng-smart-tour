from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.api.v1.admin as admin_module
from app.api.deps import get_session_factory
from app.core.security import hash_password
from app.main import app
from app.services.price_service import resolve_source_priority
from app.models import Poi, PriceRecord
from tests.api.test_chat import FakeSessionCtx

client = TestClient(app)

ADMIN_PASSWORD = "admin123"
ADMIN_PASSWORD_HASH = hash_password(ADMIN_PASSWORD)


def make_admin_user(user_id=1, username="admin", active=True):
    return SimpleNamespace(
        id=user_id,
        username=username,
        password_hash=ADMIN_PASSWORD_HASH,
        display_name="管理员",
        role="admin",
        is_active=active,
        must_change_password=False,
    )


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


class FakeUserRepo:
    users: list = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.users = []

    async def get(self, user_id):
        return next((u for u in self.users if u.id == user_id), None)

    async def get_by_username(self, username):
        return next((u for u in self.users if u.username == username), None)


class FakeAuditRepo:
    logs: list = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.logs = []

    async def append(
        self,
        operator_id,
        action,
        entity_type,
        entity_id,
        before_value=None,
        after_value=None,
    ):
        log = SimpleNamespace(
            id=len(self.logs) + 1,
            operator_id=operator_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before_value=before_value,
            after_value=after_value,
            operated_at=datetime(2026, 10, 20, 12, 0),
        )
        self.logs.append(log)
        return log

    async def list_filtered(
        self,
        operator_id=None,
        entity_type=None,
        entity_id=None,
        since=None,
        until=None,
        page=1,
        page_size=20,
    ):
        rows = list(self.logs)
        if operator_id is not None:
            rows = [l for l in rows if l.operator_id == operator_id]
        if entity_type is not None:
            rows = [l for l in rows if l.entity_type == entity_type]
        if entity_id is not None:
            rows = [l for l in rows if l.entity_id == entity_id]
        if since is not None:
            rows = [l for l in rows if l.operated_at >= since]
        if until is not None:
            rows = [l for l in rows if l.operated_at <= until]
        rows.sort(key=lambda l: -l.id)
        total = len(rows)
        start = (page - 1) * page_size
        return rows[start : start + page_size], total


class FakePriceRepo:
    records: list = []
    next_id = 1

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.records = []
        cls.next_id = 1

    async def get(self, record_id):
        return next((r for r in self.records if r.id == record_id), None)

    async def add(self, record):
        record.id = self.next_id
        self.next_id += 1
        self.records.append(record)
        return record

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


class FakePoiAdminRepo:
    pois: list = []
    next_id = 1

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.pois = []
        cls.next_id = 1

    async def get(self, poi_id):
        return next((p for p in self.pois if p.id == poi_id), None)

    async def get_by_name(self, name):
        return next((p for p in self.pois if p.name == name), None)

    async def add(self, poi):
        poi.id = self.next_id
        self.next_id += 1
        self.pois.append(poi)
        return poi


class FakeEventRepo:
    events: list = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.events = []

    async def append(
        self,
        event_type,
        entity_type,
        entity_id,
        payload,
        affected_itinerary_ids=None,
    ):
        event = SimpleNamespace(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            affected_itinerary_ids=affected_itinerary_ids or [],
            occurred_at=datetime(2026, 10, 20, 12, 0),
        )
        self.events.append(event)
        return event


class FakeItinRefRepo:
    itineraries: list = []

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.itineraries = []

    async def list_referencing(self, poi_id):
        return [
            SimpleNamespace(id=i["id"], poi_id=i["poi_id"])
            for i in self.itineraries
            if i["poi_id"] == poi_id
        ]


class FakeKnowledgeRepo:
    documents: list = []
    next_id = 1

    def __init__(self, session) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.documents = []
        cls.next_id = 1

    @classmethod
    def add_document(cls, doc):
        doc.id = cls.next_id
        cls.next_id += 1
        cls.documents.append(doc)
        return doc

    async def get(self, document_id):
        return next((d for d in self.documents if d.id == document_id), None)

    async def get_by_title(self, title):
        return [d for d in self.documents if d.title == title]

    async def list_versions(self, title):
        rows = [d for d in self.documents if d.title == title]
        return sorted(rows, key=lambda d: -d.version)

    async def list_documents(self, topic=None, status=None):
        rows = list(self.documents)
        if topic is not None:
            rows = [d for d in rows if d.topic == topic]
        if status is not None:
            rows = [d for d in rows if d.status == status]
        return sorted(rows, key=lambda d: -d.id)


class FakeChunkRepo:
    def __init__(self, session) -> None:
        pass


class FakeSuggestionService:
    calls: list = []

    def __init__(self, replan_service=None) -> None:
        pass

    @classmethod
    def reset(cls):
        cls.calls = []

    async def on_price_change(self, session_factory, poi_id, new_price, affected_ids):
        self.calls.append(("price_change", poi_id, new_price, list(affected_ids)))
        return [{"plan_id": str(affected_ids[0]) if affected_ids else None, "created": True}]

    async def on_poi_closed(self, session_factory, poi_id, affected_ids):
        self.calls.append(("poi_closed", poi_id, list(affected_ids)))
        return []

    async def on_weather_alert(self, session_factory, visit_date, alert_level):
        return []


async def fake_ingest_document(session, *, title, topic, content, source, operator_id):
    versions = [d.version for d in FakeKnowledgeRepo.documents if d.title == title]
    doc = FakeKnowledgeRepo.add_document(
        SimpleNamespace(
            title=title,
            topic=topic,
            version=max(versions) + 1 if versions else 1,
            status="有效",
            ingested_at=datetime(2026, 10, 20, 12, 0),
        )
    )
    return doc


@pytest.fixture()
def admin_env(monkeypatch):
    FakeUserRepo.reset()
    FakeAuditRepo.reset()
    FakePriceRepo.reset()
    FakePoiAdminRepo.reset()
    FakeEventRepo.reset()
    FakeItinRefRepo.reset()
    FakeKnowledgeRepo.reset()
    FakeSuggestionService.reset()
    admin_module._reset_login_guard()
    FakeUserRepo.users = [make_admin_user()]

    invalidated_prices: list[int] = []
    invalidated_pois: list[bool] = []

    async def fake_invalidate_price(poi_id):
        invalidated_prices.append(poi_id)

    async def fake_invalidate_poi():
        invalidated_pois.append(True)

    app.dependency_overrides[get_session_factory] = lambda: (lambda: FakeSessionCtx())
    monkeypatch.setattr("app.api.v1.admin.UserRepository", FakeUserRepo)
    monkeypatch.setattr("app.api.v1.admin.AuditLogRepository", FakeAuditRepo)
    monkeypatch.setattr("app.api.v1.admin.PriceRepository", FakePriceRepo)
    monkeypatch.setattr("app.api.v1.admin.PoiRepository", FakePoiAdminRepo)
    monkeypatch.setattr("app.api.v1.admin.ChangeEventRepository", FakeEventRepo)
    monkeypatch.setattr("app.api.v1.admin.ItineraryRepository", FakeItinRefRepo)
    monkeypatch.setattr("app.api.v1.admin.KnowledgeRepository", FakeKnowledgeRepo)
    monkeypatch.setattr("app.api.v1.admin.KnowledgeChunkRepository", FakeChunkRepo)
    monkeypatch.setattr("app.api.v1.admin.SuggestionService", FakeSuggestionService)
    monkeypatch.setattr("app.api.v1.admin.invalidate_price_cache", fake_invalidate_price)
    monkeypatch.setattr("app.api.v1.admin.invalidate_poi_cache", fake_invalidate_poi)
    monkeypatch.setattr("app.rag.ingestion.ingest_document", fake_ingest_document)
    yield SimpleNamespace(
        monkeypatch=monkeypatch,
        invalidated_prices=invalidated_prices,
        invalidated_pois=invalidated_pois,
    )
    app.dependency_overrides.clear()


def login(client):
    response = client.post(
        "/api/v1/admin/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def make_price_record(poi_id=1, amount=60, channel="官方渠道", current=True, status="已通过"):
    record = PriceRecord(
        poi_id=poi_id,
        price_type="门票",
        amount=amount,
        source_channel=channel,
        source_priority=resolve_source_priority(channel),
        collected_at=date(2026, 10, 1),
        valid_until=date(2026, 12, 30),
        review_status=status,
        is_current=current,
    )
    record.id = 100 + len(FakePriceRepo.records)
    FakePriceRepo.records.append(record)
    return record


# ---------- 10.1 登录与鉴权守卫 ----------


def test_login_success_returns_jwt_and_audits(admin_env):
    response = client.post(
        "/api/v1/admin/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "Bearer"
    assert payload["token"]
    assert payload["username"] == "admin"
    assert any(l.action == "admin_login" for l in FakeAuditRepo.logs)


def test_login_wrong_password_rejected(admin_env):
    response = client.post(
        "/api/v1/admin/login",
        json={"username": "admin", "password": "wrong"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == 40102


def test_login_locked_after_5_failures(admin_env):
    for _ in range(5):
        response = client.post(
            "/api/v1/admin/login",
            json={"username": "admin", "password": "wrong"},
        )
        assert response.status_code == 401
    locked = client.post(
        "/api/v1/admin/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert locked.status_code == 403
    assert locked.json()["code"] == 40301
    assert "锁定" in locked.json()["message"]


def test_admin_endpoint_requires_token(admin_env):
    response = client.get("/api/v1/admin/audits")
    assert response.status_code == 401
    assert response.json()["code"] == 40102


def test_admin_endpoint_rejects_invalid_token(admin_env):
    response = client.get(
        "/api/v1/admin/audits",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == 40102


def test_admin_endpoint_rejects_disabled_account(admin_env):
    FakeUserRepo.users = [make_admin_user(active=False)]
    response = client.post(
        "/api/v1/admin/login",
        json={"username": "admin", "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 403
    assert response.json()["code"] == 40301


# ---------- 10.2 价格提交与审核 ----------


def test_submit_price_lands_pending(admin_env):
    FakePoiAdminRepo.pois = [make_poi(1)]
    headers = login(client)
    response = client.post(
        "/api/v1/admin/prices",
        json={
            "poi_id": 1,
            "price_type": "门票",
            "amount": 80,
            "source_channel": "实地调研",
            "collected_at": "2026-10-18",
            "valid_until": "2026-12-31",
        },
        headers=headers,
    )
    assert response.status_code == 200
    record = response.json()["record"]
    assert record["review_status"] == "待审核"
    assert record["is_current"] is False
    assert record["amount"] == "80.00"
    assert any(l.action == "price_submit" for l in FakeAuditRepo.logs)


def test_submit_price_poi_not_found(admin_env):
    headers = login(client)
    response = client.post(
        "/api/v1/admin/prices",
        json={
            "poi_id": 999,
            "price_type": "门票",
            "amount": 80,
            "source_channel": "实地调研",
            "collected_at": "2026-10-18",
            "valid_until": "2026-12-31",
        },
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["code"] == 40401


def test_submit_price_invalid_type(admin_env):
    FakePoiAdminRepo.pois = [make_poi(1)]
    headers = login(client)
    response = client.post(
        "/api/v1/admin/prices",
        json={
            "poi_id": 1,
            "price_type": "住宿",
            "amount": 80,
            "source_channel": "实地调研",
            "collected_at": "2026-10-18",
            "valid_until": "2026-12-31",
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["code"] == 40001


def test_review_approve_takes_effect_and_invalidates_cache(admin_env):
    FakePoiAdminRepo.pois = [make_poi(1)]
    FakeItinRefRepo.itineraries = [{"id": 7, "poi_id": 1}]
    record = make_price_record(poi_id=1, amount=80, channel="实地调研", current=False, status="待审核")
    headers = login(client)

    response = client.post(
        "/api/v1/admin/prices/review",
        json={"record_id": record.id, "action": "approve"},
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["took_effect"] is True
    assert payload["record"]["review_status"] == "已通过"
    assert payload["record"]["is_current"] is True
    assert payload["affected_itinerary_ids"] == [7]
    assert admin_env.invalidated_prices == [1]
    assert any(e.event_type == "价格变更" for e in FakeEventRepo.events)
    assert any(l.action == "price_review_approve" for l in FakeAuditRepo.logs)
    assert FakeSuggestionService.calls == [("price_change", 1, 80.0, [7])]
    assert payload["suggestions"][0]["created"] is True


def test_review_lower_priority_record_does_not_take_effect(admin_env):
    FakePoiAdminRepo.pois = [make_poi(1)]
    make_price_record(poi_id=1, amount=60, channel="官方渠道", current=True, status="已通过")
    record = make_price_record(poi_id=1, amount=50, channel="游客上报", current=False, status="待审核")
    headers = login(client)

    response = client.post(
        "/api/v1/admin/prices/review",
        json={"record_id": record.id, "action": "approve"},
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["took_effect"] is False
    assert payload["record"]["conflict_note"]
    assert admin_env.invalidated_prices == []


def test_review_reject_keeps_record_hidden(admin_env):
    FakePoiAdminRepo.pois = [make_poi(1)]
    record = make_price_record(poi_id=1, amount=80, channel="实地调研", current=False, status="待审核")
    headers = login(client)

    response = client.post(
        "/api/v1/admin/prices/review",
        json={"record_id": record.id, "action": "reject", "remark": "照片模糊"},
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["took_effect"] is False
    assert payload["record"]["review_status"] == "已驳回"
    assert any(l.action == "price_review_reject" for l in FakeAuditRepo.logs)


def test_review_twice_conflicts(admin_env):
    FakePoiAdminRepo.pois = [make_poi(1)]
    record = make_price_record(poi_id=1, amount=80, channel="实地调研", current=False, status="已通过")
    headers = login(client)

    response = client.post(
        "/api/v1/admin/prices/review",
        json={"record_id": record.id, "action": "approve"},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["code"] == 40901


def test_review_missing_record(admin_env):
    headers = login(client)
    response = client.post(
        "/api/v1/admin/prices/review",
        json={"record_id": 424242, "action": "approve"},
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["code"] == 40401


# ---------- 10.3 POI 维护与失效/恢复 ----------


def poi_create_payload(**overrides):
    payload = {
        "name": "回龙窝历史文化街区",
        "category": "商圈",
        "tags": ["历史", "美食"],
        "longitude": 117.19,
        "latitude": 34.26,
        "visit_minutes": 60,
        "indoor": False,
        "description": "清代民居建筑群",
        "source_channel": "官方渠道",
        "collected_at": "2026-10-01",
    }
    payload.update(overrides)
    return payload


def test_create_poi_success(admin_env):
    headers = login(client)
    response = client.post("/api/v1/admin/pois", json=poi_create_payload(), headers=headers)
    assert response.status_code == 200
    poi = response.json()["poi"]
    assert poi["poi_id"] == "1"
    assert poi["status"] == "有效"
    assert poi["location"]["lng"] == 117.19
    assert any(l.action == "poi_create" for l in FakeAuditRepo.logs)
    assert admin_env.invalidated_pois == [True]


def test_create_poi_duplicate_name_conflict(admin_env):
    FakePoiAdminRepo.pois = [make_poi(1, "回龙窝历史文化街区")]
    headers = login(client)
    response = client.post("/api/v1/admin/pois", json=poi_create_payload(), headers=headers)
    assert response.status_code == 409
    assert response.json()["code"] == 40902


def test_create_poi_invalid_category(admin_env):
    headers = login(client)
    response = client.post(
        "/api/v1/admin/pois",
        json=poi_create_payload(category="住宿"),
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["code"] == 40001


def test_create_poi_coords_out_of_xuzhou(admin_env):
    headers = login(client)
    response = client.post(
        "/api/v1/admin/pois",
        json=poi_create_payload(longitude=118.5),
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["code"] == 40001


def test_update_poi_rename_conflict(admin_env):
    FakePoiAdminRepo.pois = [make_poi(1, "龟山汉墓"), make_poi(2, "汉文化景区")]
    headers = login(client)
    response = client.patch(
        "/api/v1/admin/pois",
        json={"poi_id": 1, "name": "汉文化景区"},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["code"] == 40902


def test_update_poi_not_found(admin_env):
    headers = login(client)
    response = client.patch(
        "/api/v1/admin/pois",
        json={"poi_id": 999, "visit_minutes": 120},
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["code"] == 40401


def test_update_poi_success_with_audit_trail(admin_env):
    FakePoiAdminRepo.pois = [make_poi(1)]
    headers = login(client)
    response = client.patch(
        "/api/v1/admin/pois",
        json={"poi_id": 1, "visit_minutes": 120, "indoor": True},
        headers=headers,
    )
    assert response.status_code == 200
    poi = response.json()["poi"]
    assert poi["visit_minutes"] == 120
    assert poi["indoor"] is True
    update_logs = [l for l in FakeAuditRepo.logs if l.action == "poi_update"]
    assert update_logs and update_logs[0].before_value["visit_minutes"] == 90
    assert update_logs[0].after_value["visit_minutes"] == 120


def test_poi_status_change_immediate_effect(admin_env):
    FakePoiAdminRepo.pois = [make_poi(1)]
    FakeItinRefRepo.itineraries = [{"id": 5, "poi_id": 1}, {"id": 9, "poi_id": 1}]
    headers = login(client)
    response = client.patch(
        "/api/v1/admin/pois/1/status",
        json={"status": "失效"},
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["poi_id"] == "1"
    assert payload["status"] == "失效"
    assert sorted(payload["affected_itinerary_ids"]) == [5, 9]
    events = [e for e in FakeEventRepo.events if e.event_type == "POI状态变更"]
    assert events and events[0].affected_itinerary_ids == [5, 9]
    assert admin_env.invalidated_pois == [True]
    assert any(l.action == "poi_status_change" for l in FakeAuditRepo.logs)
    assert FakeSuggestionService.calls == [("poi_closed", 1, [5, 9])]

    restore = client.patch(
        "/api/v1/admin/pois/1/status",
        json={"status": "有效"},
        headers=headers,
    )
    assert restore.status_code == 200
    assert restore.json()["status"] == "有效"
    assert FakeSuggestionService.calls == [("poi_closed", 1, [5, 9])]


def test_poi_status_change_not_found(admin_env):
    headers = login(client)
    response = client.patch(
        "/api/v1/admin/pois/999/status",
        json={"status": "失效"},
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["code"] == 40401


def test_poi_status_change_invalid_value(admin_env):
    FakePoiAdminRepo.pois = [make_poi(1)]
    headers = login(client)
    response = client.patch(
        "/api/v1/admin/pois/1/status",
        json={"status": "关闭"},
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["code"] == 40001


# ---------- 10.4 知识文档管理 ----------


def test_knowledge_upload_walks_pipeline(admin_env):
    headers = login(client)
    response = client.post(
        "/api/v1/admin/knowledge",
        json={
            "title": "云龙湖景区游览指南",
            "topic": "景点",
            "content": "云龙湖位于徐州市区南部……",
            "source": "官方渠道",
        },
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "完成"
    assert payload["version"] == 1
    assert FakeKnowledgeRepo.documents[0].title == "云龙湖景区游览指南"


def test_knowledge_edit_creates_new_version(admin_env):
    FakeKnowledgeRepo.add_document(
        SimpleNamespace(
            title="云龙湖景区游览指南",
            topic="景点",
            source="官方渠道",
            version=1,
            status="有效",
            ingested_at=datetime(2026, 10, 19, 9, 0),
        )
    )
    headers = login(client)
    response = client.patch(
        "/api/v1/admin/knowledge/1",
        json={"content": "云龙湖更新后的介绍……"},
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == 2
    assert payload["title"] == "云龙湖景区游览指南"


def test_knowledge_status_toggle_and_immediate_effect(admin_env):
    FakeKnowledgeRepo.add_document(
        SimpleNamespace(
            title="云龙湖景区游览指南",
            topic="景点",
            version=1,
            status="有效",
            ingested_at=datetime(2026, 10, 19, 9, 0),
        )
    )
    headers = login(client)
    response = client.patch(
        "/api/v1/admin/knowledge/1/status",
        json={"status": "失效"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "失效"
    assert FakeKnowledgeRepo.documents[0].status == "失效"
    assert any(l.action == "knowledge_status_change" for l in FakeAuditRepo.logs)

    restore = client.patch(
        "/api/v1/admin/knowledge/1/status",
        json={"status": "有效"},
        headers=headers,
    )
    assert restore.status_code == 200
    assert restore.json()["status"] == "有效"


def test_knowledge_status_not_found(admin_env):
    headers = login(client)
    response = client.patch(
        "/api/v1/admin/knowledge/999/status",
        json={"status": "失效"},
        headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["code"] == 40401


def test_knowledge_versions_traceable(admin_env):
    for version in (1, 2):
        FakeKnowledgeRepo.add_document(
            SimpleNamespace(
                title="云龙湖景区游览指南",
                topic="景点",
                version=version,
                status="有效" if version == 2 else "已替换",
                ingested_at=datetime(2026, 10, version, 9, 0),
            )
        )
    headers = login(client)
    response = client.get(
        "/api/v1/admin/knowledge/versions",
        params={"title": "云龙湖景区游览指南"},
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert [v["version"] for v in payload["items"]] == [2, 1]


def test_knowledge_detail_returns_content(admin_env):
    FakeKnowledgeRepo.add_document(
        SimpleNamespace(
            title="云龙湖景区游览指南",
            topic="景点",
            version=1,
            status="有效",
            source="官方渠道",
            content="云龙湖位于徐州市区南部……",
            ingested_at=datetime(2026, 10, 19, 9, 0),
        )
    )
    headers = login(client)
    response = client.get("/api/v1/admin/knowledge/1", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "云龙湖景区游览指南"
    assert payload["content"].startswith("云龙湖")
    assert payload["source"] == "官方渠道"
    assert payload["version"] == 1


def test_knowledge_detail_not_found(admin_env):
    headers = login(client)
    response = client.get("/api/v1/admin/knowledge/999", headers=headers)
    assert response.status_code == 404
    assert response.json()["code"] == 40401


def test_knowledge_versions_route_not_captured_by_detail(admin_env):
    headers = login(client)
    response = client.get(
        "/api/v1/admin/knowledge/versions",
        params={"title": "任意标题"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


# ---------- 10.5 审计查询 ----------


def test_audits_filter_by_entity_and_pagination(admin_env):
    for i in range(5):
        FakeAuditRepo.logs.append(
            SimpleNamespace(
                id=i + 1,
                operator_id=1,
                action="poi_update",
                entity_type="poi",
                entity_id=i + 1,
                before_value={"visit_minutes": 90},
                after_value={"visit_minutes": 120},
                operated_at=datetime(2026, 10, 20, 12, 0),
            )
        )
    headers = login(client)
    response = client.get(
        "/api/v1/admin/audits",
        params={"entity_type": "poi", "page": 1, "page_size": 2},
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"items", "total", "page", "page_size"}
    assert payload["total"] == 5
    assert len(payload["items"]) == 2
    item = payload["items"][0]
    assert item["operator_id"] == "1"
    assert item["before_value"] == {"visit_minutes": 90}
    assert item["after_value"] == {"visit_minutes": 120}
    assert item["operated_at"]

    filtered = client.get(
        "/api/v1/admin/audits",
        params={"entity_type": "poi", "entity_id": 3},
        headers=headers,
    )
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["entity_id"] == "3"


def test_audits_time_window_filter(admin_env):
    FakeAuditRepo.logs.append(
        SimpleNamespace(
            id=1,
            operator_id=1,
            action="poi_create",
            entity_type="poi",
            entity_id=1,
            before_value=None,
            after_value=None,
            operated_at=datetime(2026, 10, 20, 8, 0),
        )
    )
    FakeAuditRepo.logs.append(
        SimpleNamespace(
            id=2,
            operator_id=1,
            action="poi_update",
            entity_type="poi",
            entity_id=1,
            before_value=None,
            after_value=None,
            operated_at=datetime(2026, 10, 20, 16, 0),
        )
    )
    headers = login(client)
    response = client.get(
        "/api/v1/admin/audits",
        params={"since": "2026-10-20T10:00:00", "until": "2026-10-20T23:00:00"},
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    actions = {item["action"] for item in payload["items"]}
    assert "poi_create" not in actions
    assert "poi_update" in actions
    for item in payload["items"]:
        assert "2026-10-20T10:00:00" <= item["operated_at"] <= "2026-10-20T23:00:00"


def test_audits_invalid_page_rejected(admin_env):
    headers = login(client)
    response = client.get(
        "/api/v1/admin/audits",
        params={"page": 0},
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["code"] == 40001