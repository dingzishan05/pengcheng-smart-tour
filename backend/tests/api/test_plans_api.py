from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agent.state import AgentResponse, StructuredRequest
from app.core.errors import BizError, ErrorCode
from app.main import app
from app.optimizer.models import Goal, ItineraryPlan
from tests.api.test_chat import (
    FakeConvRepo,
    FakeEventRepo,
    FakeItemRepo,
    FakeItinRepo,
    FakeMsgRepo,
    FakeSessionCtx,
)

client = TestClient(app)


class FakeReplanService:
    last_call: dict | None = None
    outcome: dict | None = None

    def __init__(self, problem_builder=None, itinerary_service=None) -> None:
        pass

    async def refresh(self, session_factory, conversation_id, plan_id, trigger, changes):
        FakeReplanService.last_call = {
            "conversation_id": conversation_id,
            "plan_id": plan_id,
            "trigger": trigger,
            "changes": changes.model_dump(),
        }
        if FakeReplanService.outcome is not None:
            return FakeReplanService.outcome
        raise BizError(ErrorCode.NO_FEASIBLE_PLAN, "当前约束下无可行行程", detail={"advice": {"reasons": ["预算过低"]}})


def node_of(update: dict):
    def make(*args, **kwargs):
        async def node(state):
            return dict(update)

        return node

    return make


@pytest.fixture()
def plans_env(monkeypatch):
    FakeConvRepo.reset()
    FakeMsgRepo.reset()
    FakeItinRepo.reset()
    FakeItemRepo.reset()
    FakeEventRepo.reset()
    FakeReplanService.last_call = None
    FakeReplanService.outcome = None

    from app.api.deps import get_session_factory

    app.dependency_overrides[get_session_factory] = lambda: (lambda: FakeSessionCtx())
    for module, name in [
        ("app.api.v1.plans", "ItineraryRepository"),
        ("app.api.v1.plans", "ItineraryItemRepository"),
        ("app.api.v1.plans", "ChangeEventRepository"),
    ]:
        target = {
            "ItineraryRepository": FakeItinRepo,
            "ItineraryItemRepository": FakeItemRepo,
            "ChangeEventRepository": FakeEventRepo,
        }[name]
        monkeypatch.setattr(f"{module}.{name}", target)
    monkeypatch.setattr("app.api.deps.ConversationRepository", FakeConvRepo)
    monkeypatch.setattr("app.services.replan_service.ItineraryRepository", lambda session: FakeItinRepo())
    yield monkeypatch
    app.dependency_overrides.clear()


def wire_generation_nodes(monkeypatch, final_plans, final_response=None):
    monkeypatch.setattr(
        "app.api.v1.plans.make_retrieve_pois_node",
        lambda factory: node_of(
            {
                "structured_request": None,
                "candidate_pois": [SimpleNamespace(poi_id=1)],
                "visit_date": "2026-10-21",
            }
        )(),
    )
    monkeypatch.setattr(
        "app.api.v1.plans.make_fetch_live_data_node",
        lambda: node_of({"route_matrix": {}, "weather": None, "data_degraded": False})(),
    )
    monkeypatch.setattr(
        "app.api.v1.plans.make_generate_candidates_node",
        lambda: node_of({"candidate_plans": [(1, 2)]})(),
    )
    monkeypatch.setattr(
        "app.api.v1.plans.make_optimize_node", lambda: node_of({"plans": final_plans})()
    )
    monkeypatch.setattr("app.api.v1.plans.make_validate_node", lambda: node_of({})())
    monkeypatch.setattr(
        "app.api.v1.plans.make_explain_node",
        lambda *a, **k: node_of(
            {
                "final_response": final_response
                or AgentResponse(response_type="plans", content="已生成三种方案", plans=final_plans)
            }
        )(),
    )


def make_plans():
    return [
        ItineraryPlan(goal=Goal.SAVE_MONEY, items=[], total_cost=188, total_minutes=540, transit_minutes=95),
        ItineraryPlan(goal=Goal.SAVE_TIME, items=[], total_cost=320, total_minutes=480, transit_minutes=60),
        ItineraryPlan(goal=Goal.BALANCED, items=[], total_cost=240, total_minutes=520, transit_minutes=80),
    ]


def create_conversation(token="token-1"):
    conv = SimpleNamespace(
        id=1, session_token=token, title=None, status="active", last_active_at=None
    )
    FakeConvRepo.conversations.append(conv)
    return conv


def test_post_plans_generates_three_drafts(plans_env):
    create_conversation()
    plans = make_plans()
    wire_generation_nodes(plans_env, plans)

    response = client.post(
        "/api/v1/plans",
        json={
            "request": {
                "duration_hours": 8,
                "budget": 300,
                "interests": ["历史"],
                "short_trip": False,
            }
        },
        cookies={"pczy_session": "token-1"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["conversation_id"] == "1"
    assert len(payload["plans"]) == 3
    assert len(payload["plan_details"]) == 3
    detail = payload["plan_details"][0]
    assert detail["status"] == "草稿"
    assert detail["total_cost"] == "188.00"
    assert detail["explanation"] == "已生成三种方案"
    assert detail["items"] == []
    assert len(FakeItinRepo.itineraries) == 3


def test_post_plans_infeasible_returns_50001_with_advice(plans_env):
    from app.optimizer.models import (
        InfeasibilityAdvice,
        InfeasibilityReport,
        InfeasibleReason,
        InfeasibleReasonKind,
    )

    create_conversation()
    monkeypatch = plans_env
    monkeypatch.setattr(
        "app.api.v1.plans.make_retrieve_pois_node",
        lambda factory: node_of(
            {
                "candidate_pois": [],
                "infeasibility": InfeasibilityReport(
                    reasons=[
                        InfeasibleReason(
                            kind=InfeasibleReasonKind.BUDGET, detail="预算低于最低可行组合"
                        )
                    ],
                    advice=InfeasibilityAdvice(min_budget_suggestion="180.00", free_first=True),
                ),
            }
        )(),
    )

    response = client.post(
        "/api/v1/plans",
        json={"request": {"duration_hours": 8, "budget": 50, "interests": ["历史"]}},
        cookies={"pczy_session": "token-1"},
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == 50001
    assert payload["detail"]["advice"]["min_budget_suggestion"] == "180.00"
    assert payload["detail"]["advice"]["reasons"] == ["预算低于最低可行组合"]
    assert FakeItinRepo.itineraries == []


def test_post_plans_invalid_duration(plans_env):
    response = client.post(
        "/api/v1/plans",
        json={"request": {"duration_hours": 1, "budget": 300, "interests": ["历史"]}},
    )
    assert response.status_code == 422
    assert response.json()["code"] == 40001


def test_post_plans_missing_request(plans_env):
    response = client.post("/api/v1/plans", json={})
    assert response.status_code == 422
    assert response.json()["code"] == 40001


def test_confirm_plan_state_machine(plans_env):
    create_conversation()
    itinerary = SimpleNamespace(
        id=5,
        conversation_id=1,
        plan_type="省钱",
        status="草稿",
        total_cost=188,
        total_minutes=540,
        request_snapshot={"visit_date": "2026-10-21"},
        solver_note=None,
        data_time=None,
        version_lineage=1,
    )
    FakeItinRepo.itineraries.append(itinerary)

    response = client.post(
        "/api/v1/plans/5/confirm?conversation_id=1",
        cookies={"pczy_session": "token-1"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["plan_id"] == "5"
    assert payload["status"] == "confirmed"
    assert payload["plan_detail"]["status"] == "已确认"
    assert FakeEventRepo.events[-1]["payload"]["action"] == "confirm"

    second = client.post(
        "/api/v1/plans/5/confirm?conversation_id=1",
        cookies={"pczy_session": "token-1"},
    )
    assert second.status_code == 409
    assert second.json()["code"] == 40901


def test_confirm_plan_not_in_conversation(plans_env):
    create_conversation()
    other = SimpleNamespace(
        id=9,
        conversation_id=2,
        plan_type="省钱",
        status="草稿",
        total_cost=100,
        total_minutes=300,
        request_snapshot={},
        solver_note=None,
        data_time=None,
        version_lineage=1,
    )
    FakeItinRepo.itineraries.append(other)
    response = client.post(
        "/api/v1/plans/9/confirm?conversation_id=1",
        cookies={"pczy_session": "token-1"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == 40401


def test_refresh_plan_returns_new_old_diff(plans_env):
    create_conversation()
    itinerary = SimpleNamespace(
        id=5,
        conversation_id=1,
        plan_type="省钱",
        status="已确认",
        total_cost=300,
        total_minutes=540,
        request_snapshot={"visit_date": "2026-10-21"},
        solver_note=None,
        data_time=None,
        version_lineage=1,
    )
    FakeItinRepo.itineraries.append(itinerary)
    FakeItemRepo.by_itinerary[5] = []
    plans_env.setattr("app.api.v1.plans.ReplanService", FakeReplanService)
    FakeReplanService.outcome = {
        "new_plan": {"plan_id": "6", "status": "挂起建议"},
        "old_plan": {"plan_id": "5", "status": "已确认"},
        "diff": {
            "removed": ["龟山汉墓"],
            "added": ["汉文化景区"],
            "cost_delta": "-40.00",
            "reason": "预算由300调整为150",
        },
        "conflict": None,
        "data_time": None,
    }

    response = client.post(
        "/api/v1/plans/5/refresh",
        json={
            "conversation_id": "1",
            "trigger": "user_change",
            "changes": {"budget": "150.00"},
        },
        cookies={"pczy_session": "token-1"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["diff"]["removed"] == ["龟山汉墓"]
    assert payload["diff"]["cost_delta"] == "-40.00"
    assert payload["conflict"] is None
    assert FakeReplanService.last_call["changes"]["budget"] == 150.0
    assert FakeReplanService.last_call["plan_id"] == 5


def test_refresh_plan_infeasible_keeps_original(plans_env):
    create_conversation()
    itinerary = SimpleNamespace(
        id=5,
        conversation_id=1,
        plan_type="省钱",
        status="已确认",
        total_cost=300,
        total_minutes=540,
        request_snapshot={"visit_date": "2026-10-21"},
        solver_note=None,
        data_time=None,
        version_lineage=1,
    )
    FakeItinRepo.itineraries.append(itinerary)
    plans_env.setattr("app.api.v1.plans.ReplanService", FakeReplanService)

    response = client.post(
        "/api/v1/plans/5/refresh",
        json={
            "conversation_id": "1",
            "trigger": "user_change",
            "changes": {"budget": "10.00"},
        },
        cookies={"pczy_session": "token-1"},
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == 50001
    assert payload["detail"]["advice"]["reasons"] == ["预算过低"]
    assert itinerary.status == "已确认"


def test_refresh_plan_invalid_trigger(plans_env):
    response = client.post(
        "/api/v1/plans/5/refresh",
        json={"trigger": "unknown", "changes": {"budget": "100"}},
    )
    assert response.status_code == 422
    assert response.json()["code"] == 40001


def test_refresh_plan_empty_changes(plans_env):
    response = client.post(
        "/api/v1/plans/5/refresh",
        json={"trigger": "user_change", "changes": {}},
    )
    assert response.status_code == 422

def test_get_plan_detail_returns_items_and_meta(plans_env):
    create_conversation()
    FakeItinRepo.itineraries.append(
        SimpleNamespace(
            id=7,
            conversation_id=1,
            plan_type="省钱",
            status="草稿",
            total_cost=188,
            total_minutes=540,
            request_snapshot={"visit_date": "2026-10-21"},
            solver_note=None,
            data_time=None,
            version_lineage=1,
        )
    )
    FakeItemRepo.by_itinerary[7] = [
        SimpleNamespace(
            seq=1,
            activity_type="游览",
            poi_id=3,
            start_at=None,
            end_at=None,
            transport_mode=None,
            transport_minutes=None,
            estimated_cost=60,
            cost_meta={"source_channel": "官方渠道", "collected_at": "2026-10-01", "is_reference": True},
        )
    ]

    response = client.get(
        "/api/v1/plans/7?conversation_id=1",
        cookies={"pczy_session": "token-1"},
    )
    assert response.status_code == 200
    detail = response.json()
    assert detail["plan_id"] == "7"
    assert detail["plan_type"] == "省钱"
    assert detail["items"][0]["cost_meta"]["is_reference"] is True
    assert detail["explanation"]


def test_get_plan_detail_foreign_conversation_404(plans_env):
    create_conversation()
    FakeItinRepo.itineraries.append(
        SimpleNamespace(
            id=8,
            conversation_id=2,
            plan_type="省时",
            status="草稿",
            total_cost=300,
            total_minutes=480,
            request_snapshot={},
            solver_note=None,
            data_time=None,
            version_lineage=1,
        )
    )

    response = client.get(
        "/api/v1/plans/8?conversation_id=1",
        cookies={"pczy_session": "token-1"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == 40401
