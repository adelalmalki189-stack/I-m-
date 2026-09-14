from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app
from app.models.enums import SignalDirection, SignalStatus
from app.models.instruments import Instrument
from app.models.signals import Signal
from app.models.strategy_parameters import StrategyParameters

CALC_VERSION = "v0.1.0-trades-api"


async def _seed_signal(db_session) -> Signal:
    db_session.add(Instrument(symbol="NVDA", exchange="SMART", is_active=True))
    db_session.add(
        StrategyParameters(version=CALC_VERSION, parameters={}, effective_from=datetime(2026, 3, 1, tzinfo=UTC))
    )
    await db_session.flush()
    signal = Signal(
        timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=UTC), symbol="NVDA", direction=SignalDirection.CALL,
        status=SignalStatus.ENTRY, calc_version=CALC_VERSION,
    )
    db_session.add(signal)
    await db_session.commit()
    return signal


async def test_create_and_fetch_trade(db_session):
    signal = await _seed_signal(db_session)

    with TestClient(app) as client:
        create_response = client.post(
            "/trades",
            json={
                "signal_id": signal.signal_id,
                "entry_time": "2026-03-01T10:05:00+00:00",
                "entry_price": "2.10",
                "quantity": 3,
            },
        )
        assert create_response.status_code == 201
        trade_id = create_response.json()["trade_id"]

        get_response = client.get(f"/trades/{trade_id}")
        assert get_response.status_code == 200
        body = get_response.json()
        assert body["quantity"] == 3
        assert body["user_exit_price"] is None


async def test_create_trade_404_for_unknown_signal(db_session):
    with TestClient(app) as client:
        response = client.post(
            "/trades",
            json={
                "signal_id": "does-not-exist",
                "entry_time": "2026-03-01T10:05:00+00:00",
                "entry_price": "2.10",
                "quantity": 1,
            },
        )
    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "SIGNAL_NOT_FOUND"


async def test_exit_trade_computes_realized_pnl(db_session):
    signal = await _seed_signal(db_session)

    with TestClient(app) as client:
        create_response = client.post(
            "/trades",
            json={
                "signal_id": signal.signal_id,
                "entry_time": "2026-03-01T10:05:00+00:00",
                "entry_price": "2.00",
                "quantity": 2,
            },
        )
        trade_id = create_response.json()["trade_id"]

        exit_response = client.put(
            f"/trades/{trade_id}",
            json={
                "exit_time": "2026-03-01T11:00:00+00:00",
                "exit_price": "3.00",
                "exit_reason": "TARGET_HIT",
            },
        )

    assert exit_response.status_code == 200
    body = exit_response.json()
    # (3.00 - 2.00) * 2 contracts * 100 multiplier = 200
    assert Decimal(body["realized_pnl"]) == Decimal(200)
    assert body["exit_reason"] == "TARGET_HIT"


async def test_list_trades_filters_by_date_range(db_session):
    signal = await _seed_signal(db_session)

    with TestClient(app) as client:
        client.post(
            "/trades",
            json={
                "signal_id": signal.signal_id,
                "entry_time": "2026-03-01T10:05:00+00:00",
                "entry_price": "2.00",
                "quantity": 1,
            },
        )
        response = client.get("/trades", params={"from": "2026-03-01T00:00:00+00:00"})

    assert response.status_code == 200
    assert len(response.json()) == 1
