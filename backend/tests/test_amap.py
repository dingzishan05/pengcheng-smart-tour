import pytest

from app.clients import amap
from app.clients.amap import AmapUnavailableError


def _fake_response(payload):
    class R:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    return R()


async def test_direction_transit_uses_v5_cost_duration_and_adcode(monkeypatch):
    """高德 v5 公交：耗时在 transit.cost.duration；city1/city2 必须传 adcode。"""
    captured = {}

    async def fake_get(self, url, params=None, **kwargs):
        captured.update(params or {})
        return _fake_response(
            {
                "status": "1",
                "route": {
                    "transits": [
                        {
                            "cost": {"duration": "1200", "transit_fee": "2.0"},
                            "distance": "3500",
                        }
                    ]
                },
            }
        )

    monkeypatch.setattr(amap.httpx.AsyncClient, "get", fake_get)
    result = await amap.direction("117.1,34.2", "117.2,34.3", "公交")
    assert result["mode"] == "公交"
    assert result["duration_minutes"] == 20
    assert result["fare"] == "2.0"
    assert captured["city1"] == amap.CITY_ADCODE
    assert captured["city2"] == amap.CITY_ADCODE


async def test_direction_transit_unavailable(monkeypatch):
    async def fake_get(*args, **kwargs):
        return _fake_response({"status": "1", "route": {"transits": []}})

    monkeypatch.setattr(amap.httpx.AsyncClient, "get", fake_get)
    result = await amap.direction("117.1,34.2", "117.2,34.3", "公交")
    assert result["unavailable"] is True
    assert "duration_minutes" not in result


async def test_direction_driving_v5_cost_duration(monkeypatch):
    """高德 v5 驾车/步行：时长在 path.cost.duration（秒），distance 在顶层。"""

    async def fake_get(*args, **kwargs):
        return _fake_response(
            {
                "status": "1",
                "route": {
                    "paths": [
                        {
                            "distance": "4000",
                            "cost": {"duration": "1696", "tolls": "0"},
                        }
                    ]
                },
            }
        )

    monkeypatch.setattr(amap.httpx.AsyncClient, "get", fake_get)
    result = await amap.direction("117.1,34.2", "117.2,34.3", "驾车")
    assert result["duration_minutes"] == 28
    assert result["distance_meters"] == 4000


async def test_direction_walking_v5_cost_duration(monkeypatch):
    async def fake_get(*args, **kwargs):
        return _fake_response(
            {
                "status": "1",
                "route": {"paths": [{"distance": "16590", "cost": {"duration": "13272"}}]},
            }
        )

    monkeypatch.setattr(amap.httpx.AsyncClient, "get", fake_get)
    result = await amap.direction("117.1,34.2", "117.2,34.3", "步行")
    assert result["duration_minutes"] == 221
    assert result["distance_meters"] == 16590


async def test_direction_legacy_top_level_duration(monkeypatch):
    """兼容旧格式（顶层 duration），避免第三方代理返回旧结构时再次整链路失效。"""

    async def fake_get(*args, **kwargs):
        return _fake_response(
            {"status": "1", "route": {"paths": [{"duration": 600, "distance": 4000}]}}
        )

    monkeypatch.setattr(amap.httpx.AsyncClient, "get", fake_get)
    result = await amap.direction("117.1,34.2", "117.2,34.3", "步行")
    assert result["duration_minutes"] == 10


async def test_direction_amap_status_error(monkeypatch):
    async def fake_get(*args, **kwargs):
        return _fake_response({"status": "0", "info": "INVALID_USER_KEY"})

    monkeypatch.setattr(amap.httpx.AsyncClient, "get", fake_get)
    with pytest.raises(AmapUnavailableError):
        await amap.direction("117.1,34.2", "117.2,34.3", "驾车")


def test_token_bucket_rate():
    bucket = amap.TokenBucket(10)
    assert bucket.capacity == 10.0
