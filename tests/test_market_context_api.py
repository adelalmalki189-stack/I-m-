from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.deps import get_redis_client
from app.core.market_context_cache import set_cached_market_context
from app.main import app
from tests.fakes.fake_redis import FakeRedis


async def test_market_context_returns_published_data():
    fake = FakeRedis()
    await set_cached_market_context(
        fake,
        {
            "timestamp": "2026-03-01T10:00:00+00:00",
            "spy": {"price": "512.30", "regime": "BULLISH"},
            "qqq": {"price": "438.10", "regime": "BULLISH"},
            "vix": {"value": "14.20", "regime": "NORMAL"},
            "overall_market_regime": "SUPPORTIVE",
        },
    )
    app.dependency_overrides[get_redis_client] = lambda: fake
    try:
        with TestClient(app) as client:
            response = client.get("/market/context")
    finally:
        app.dependency_overrides.pop(get_redis_client, None)

    assert response.status_code == 200
    body = response.json()
    assert body["overall_market_regime"] == "SUPPORTIVE"
    assert body["spy"]["regime"] == "BULLISH"


async def test_market_context_503_when_not_published():
    fake = FakeRedis()
    app.dependency_overrides[get_redis_client] = lambda: fake
    try:
        with TestClient(app) as client:
            response = client.get("/market/context")
    finally:
        app.dependency_overrides.pop(get_redis_client, None)

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "MARKET_CONTEXT_UNAVAILABLE"
