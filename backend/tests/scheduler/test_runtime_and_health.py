from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.api.v1.health as health_mod
import app.scheduler.runtime as runtime
from app.main import app

client = TestClient(app)


def test_scheduler_lifecycle_registers_five_jobs():
    async def scenario():
        await runtime.start_scheduler()
        try:
            assert runtime.scheduler_running() is True
            status = runtime.scheduler_status()
            assert status["running"] is True
            assert {job["id"] for job in status["jobs"]} == set(runtime.JOB_IDS)
        finally:
            await runtime.shutdown_scheduler()

    asyncio.run(scenario())
    assert runtime.scheduler_running() is False


def test_build_scheduler_uses_cron_schedules():
    scheduler = runtime.build_scheduler(FakeSessionFactory())
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == set(runtime.JOB_IDS)
    expiry_trigger = jobs["expiry_patrol"].trigger
    assert str(expiry_trigger).find("hour='3'") != -1 or "3" in str(expiry_trigger)
    assert jobs["conversation_cleanup"].trigger is not None


def test_trigger_job_dispatches_manually(monkeypatch):
    async def fake_job(session_factory=None):
        return {"done": True}

    monkeypatch.setattr(runtime, "JOB_FUNCS", {"demo_job": fake_job})

    result = asyncio.run(runtime.trigger_job("demo_job", FakeSessionFactory()))

    assert result == {"job_id": "demo_job", "result": {"done": True}}


def test_trigger_job_unknown_id():
    with pytest.raises(KeyError):
        asyncio.run(runtime.trigger_job("nope", FakeSessionFactory()))


@pytest.fixture()
def health_env(monkeypatch):
    monkeypatch.setattr(
        health_mod,
        "_check_db",
        _async_true,
    )
    monkeypatch.setattr(health_mod, "ping_redis", _async_true)
    monkeypatch.setattr(
        health_mod, "scheduler_status", lambda: {"running": True, "jobs": []}
    )
    yield monkeypatch


async def _async_true():
    return True


async def _async_false():
    return False


class FakeSessionFactory:
    def __call__(self):
        return _NoopSession()


class _NoopSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def commit(self):
        return None


def test_health_ok_with_scheduler(health_env):
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["db"] is True
    assert payload["redis"] is True
    assert payload["scheduler"] is True
    metrics = payload["metrics"]
    assert {"requests_total", "errors_total", "error_rate", "p50_ms", "p90_ms", "cache_hit_rate", "llm_calls_total"} <= set(metrics)


def test_health_degraded_without_scheduler(health_env):
    health_env.setattr(
        health_mod, "scheduler_status", lambda: {"running": False, "jobs": []}
    )

    response = client.get("/health")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["scheduler"] is False