"""Phase 3 completion criterion (docs/IMPLEMENTATION_PLAN_V0.1.md):

"GET /signals/{id}/contracts يُعيد عقوداً مرتبة فعلياً مطابقة لمثال §15
من المواصفة الأصلية (Contract A/B/C)."
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app
from app.models.enums import SignalDirection, SignalStatus
from app.models.instruments import Instrument
from app.models.signals import Signal
from app.models.strategy_parameters import StrategyParameters
from tests.engines.factories_options import make_contract, make_quote
from tests.fakes.fake_data_provider import FakeDataProvider

CALC_VERSION = "v0.1.0-contracts-api"


async def test_contracts_endpoint_returns_best_first_ranking(db_session):
    db_session.add(Instrument(symbol="NVDA", exchange="SMART", is_active=True))
    db_session.add(
        StrategyParameters(version=CALC_VERSION, parameters={}, effective_from=datetime(2026, 3, 1, tzinfo=UTC))
    )
    await db_session.flush()

    signal = Signal(
        timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        symbol="NVDA",
        direction=SignalDirection.CALL,
        status=SignalStatus.ENTRY,
        calc_version=CALC_VERSION,
    )
    db_session.add(signal)
    await db_session.commit()

    provider = FakeDataProvider()
    # Contract A: best across the board. Contract B: acceptable but worse.
    # Contract C: passes gates but weakest. Contract D: fails the spread
    # Hard Gate and must be excluded entirely from the response.
    contract_a = make_contract(contract_id="A", symbol="NVDA", strike=190, dte=18, expiration=date(2026, 4, 17))
    contract_b = make_contract(contract_id="B", symbol="NVDA", strike=195, dte=40, expiration=date(2026, 5, 9))
    contract_c = make_contract(contract_id="C", symbol="NVDA", strike=200, dte=44, expiration=date(2026, 5, 13))
    contract_d = make_contract(contract_id="D", symbol="NVDA", strike=205, dte=21, expiration=date(2026, 4, 17))

    provider.option_chains["NVDA"] = [contract_a, contract_b, contract_c, contract_d]
    provider.option_quotes["A"] = make_quote(
        contract_id="A", bid=2.08, ask=2.10, delta=0.58, open_interest=8000, volume=2000
    )
    provider.option_quotes["B"] = make_quote(
        contract_id="B", bid=1.86, ask=1.95, delta=0.42, open_interest=150, volume=120
    )
    provider.option_quotes["C"] = make_quote(
        contract_id="C", bid=1.90, ask=1.98, delta=0.69, open_interest=100, volume=100
    )
    provider.option_quotes["D"] = make_quote(
        contract_id="D", bid=1.50, ask=2.00, delta=0.55, open_interest=500, volume=300
    )

    app.state.data_provider = provider
    try:
        with TestClient(app) as client:
            response = client.get(f"/signals/{signal.signal_id}/contracts")
    finally:
        del app.state.data_provider

    assert response.status_code == 200
    body = response.json()
    assert [c["contract_id"] for c in body] == ["A", "B", "C"]
    scores = [Decimal(c["score"]) for c in body]
    assert scores[0] > scores[1] > scores[2]
    assert body[0]["premium"] == "2.09"  # Decimal serializes as a JSON string, not a float


async def test_contracts_endpoint_404_for_unknown_signal(db_session):
    with TestClient(app) as client:
        response = client.get("/signals/does-not-exist/contracts")
    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "SIGNAL_NOT_FOUND"


async def test_contracts_endpoint_503_without_data_provider(db_session):
    db_session.add(Instrument(symbol="AMD", exchange="SMART", is_active=True))
    db_session.add(
        StrategyParameters(version=CALC_VERSION, parameters={}, effective_from=datetime(2026, 3, 1, tzinfo=UTC))
    )
    await db_session.flush()
    signal = Signal(
        timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=UTC), symbol="AMD", direction=SignalDirection.PUT,
        status=SignalStatus.READY, calc_version=CALC_VERSION,
    )
    db_session.add(signal)
    await db_session.commit()

    assert getattr(app.state, "data_provider", None) is None
    with TestClient(app) as client:
        response = client.get(f"/signals/{signal.signal_id}/contracts")
    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "DATA_PROVIDER_UNAVAILABLE"
