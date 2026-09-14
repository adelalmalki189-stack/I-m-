from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.models.enums import EventSeverity, EventType
from app.models.market_events import MarketEvent
from app.models.strategy_parameters import StrategyParameters


async def test_upcoming_events_excludes_past_events(db_session):
    db_session.add(
        MarketEvent(
            symbol="NVDA", event_type=EventType.EARNINGS,
            event_time=datetime.now(UTC) + timedelta(days=2),
            severity=EventSeverity.HIGH, source="test",
        )
    )
    db_session.add(
        MarketEvent(
            symbol="NVDA", event_type=EventType.EARNINGS,
            event_time=datetime.now(UTC) - timedelta(days=2),  # past — must be excluded
            severity=EventSeverity.LOW, source="test",
        )
    )
    await db_session.commit()

    with TestClient(app) as client:
        response = client.get("/events/upcoming", params={"symbol": "NVDA"})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["severity"] == "HIGH"


async def test_active_strategy_parameters_returns_most_recent(db_session):
    db_session.add(
        StrategyParameters(version="v1", parameters={"a": 1}, effective_from=datetime(2026, 1, 1, tzinfo=UTC))
    )
    db_session.add(
        StrategyParameters(version="v2", parameters={"a": 2}, effective_from=datetime(2026, 2, 1, tzinfo=UTC))
    )
    await db_session.commit()

    with TestClient(app) as client:
        response = client.get("/strategy-parameters")
        specific = client.get("/strategy-parameters/v1")

    assert response.status_code == 200
    assert response.json()["version"] == "v2"
    assert specific.status_code == 200
    assert specific.json()["parameters"] == {"a": 1}


async def test_strategy_parameters_404_for_unknown_version(db_session):
    with TestClient(app) as client:
        response = client.get("/strategy-parameters/does-not-exist")
    assert response.status_code == 404
