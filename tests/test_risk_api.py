from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app
from app.models.enums import SignalDirection, SignalStatus
from app.models.instruments import Instrument
from app.models.risk import RiskPlan
from app.models.signals import Signal
from app.models.strategy_parameters import StrategyParameters
from app.models.trades import Trade

CALC_VERSION = "v0.1.0-risk-api"


async def test_get_risk_config_creates_default_row(db_session):
    with TestClient(app) as client:
        response = client.get("/risk/config")
    assert response.status_code == 200
    body = response.json()
    assert Decimal(body["account_size"]) == Decimal(2000)
    assert Decimal(body["risk_percent"]) == Decimal(1)


async def test_put_risk_config_updates_account_size(db_session):
    with TestClient(app) as client:
        response = client.put("/risk/config", json={"account_size": "50000", "risk_percent": "2"})
    assert response.status_code == 200
    body = response.json()
    assert Decimal(body["account_size"]) == Decimal(50000)
    assert Decimal(body["risk_percent"]) == Decimal(2)


async def test_portfolio_status_reflects_open_signal_risk(db_session):
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
    await db_session.flush()

    db_session.add(
        RiskPlan(
            signal_id=signal.signal_id, account_size=Decimal(10000), risk_percent=Decimal(1),
            max_risk=Decimal(100), entry_price=Decimal(2), stop_price=Decimal(1), target_price=Decimal(4),
            risk_per_contract=Decimal(100), recommended_quantity=1, max_loss=Decimal(100),
            potential_profit=Decimal(200), r_multiple=Decimal(2),
        )
    )
    await db_session.commit()

    with TestClient(app) as client:
        response = client.get("/risk/portfolio-status")

    assert response.status_code == 200
    body = response.json()
    assert body["open_signals_count"] == 1


async def test_portfolio_status_daily_loss_hit_true_when_losses_exceed_limit(db_session):
    db_session.add(Instrument(symbol="AMD", exchange="SMART", is_active=True))
    db_session.add(
        StrategyParameters(version=CALC_VERSION, parameters={}, effective_from=datetime(2026, 3, 1, tzinfo=UTC))
    )
    await db_session.flush()
    signal = Signal(
        timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=UTC), symbol="AMD", direction=SignalDirection.PUT,
        status=SignalStatus.INVALIDATED, calc_version=CALC_VERSION,
    )
    db_session.add(signal)
    await db_session.flush()

    today = datetime.now(UTC)
    db_session.add(
        Trade(
            signal_id=signal.signal_id, user_entry_time=today, user_entry_price=Decimal(2),
            quantity=1, user_exit_time=today, user_exit_price=Decimal("0.01"),
            realized_pnl=Decimal(-10000),  # deliberately huge loss to guarantee exceeding any % limit
        )
    )
    await db_session.commit()

    with TestClient(app) as client:
        response = client.get("/risk/portfolio-status")

    assert response.status_code == 200
    body = response.json()
    assert body["daily_max_loss_hit"] is True
