"""Tests for the live Scanner Loop (app/services/scanner_loop.py) — the
orchestrator tying the DataProvider, every engine, the database, the
Market Context cache, Alerting, and the WS channel together.

Uses FakeDataProvider (tests-only, never importable from app/) and the
already-verified synthetic multi-day generator from Phase 7
(app/replay/synthetic_market.py) to drive `handle_candle` directly —
the same "call the real function with synthetic candles" pattern
app/replay/harness.py itself established, just now against the real
database and real WS/alerting instead of returning in-memory results.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models.enums import OverallMarketRegime, SessionType, SignalDirection, SignalStatus
from app.models.notifications import NotificationLog
from app.models.risk import PortfolioRiskConfig, RiskPlan
from app.models.signal_snapshots import SignalSnapshot
from app.models.signals import Signal
from app.models.strategy_parameters import StrategyParameters
from app.models.trading_calendar import TradingCalendarDay
from app.replay.synthetic_market import BARS_PER_SESSION, generate_symbol_history
from app.services.scanner_loop import ScannerLoop
from tests.engines.factories_options import make_contract, make_quote
from tests.fakes.fake_data_provider import FakeDataProvider
from tests.fakes.fake_redis import FakeRedis

CALC_VERSION = "v0.1.0-scanner-loop-test"
BASE_OPEN = datetime(2026, 1, 5, 9, 30, tzinfo=UTC)


async def _seed_calendar(db_session, num_days: int) -> None:
    for day_index in range(num_days):
        session_open = BASE_OPEN + timedelta(days=day_index)
        day = session_open.date()
        db_session.add(
            TradingCalendarDay(
                date=day, session_type=SessionType.FULL,
                market_open=session_open,
                market_close=session_open + timedelta(minutes=5 * BARS_PER_SESSION),
                source="TEST",
            )
        )
    await db_session.commit()


async def _seed_strategy_parameters(db_session) -> None:
    db_session.add(
        StrategyParameters(version=CALC_VERSION, parameters={}, effective_from=BASE_OPEN)
    )
    await db_session.commit()


def _make_loop(
    *, provider: FakeDataProvider, redis_client: FakeRedis, tracked_symbols: list[str], app_state=None
) -> ScannerLoop:
    return ScannerLoop(
        provider=provider, redis_client=redis_client, tracked_symbols=tracked_symbols,
        context_symbols=("SPY", "QQQ"), calc_version=CALC_VERSION, app_state=app_state,
    )


async def _get_call_entry_signals(db_session, symbol: str) -> list[Signal]:
    result = await db_session.execute(
        select(Signal).where(
            Signal.symbol == symbol, Signal.direction == SignalDirection.CALL, Signal.status == SignalStatus.ENTRY
        )
    )
    return list(result.scalars().all())


def _seed_option_chain(provider: FakeDataProvider, symbol: str) -> None:
    contract = make_contract(contract_id=f"{symbol}_C1", symbol=symbol, dte=21)
    quote = make_quote(contract_id=f"{symbol}_C1", bid=1.95, ask=2.00, delta=0.55, open_interest=5000, volume=1000)
    provider.option_chains[symbol] = [contract]
    provider.option_quotes[f"{symbol}_C1"] = quote


async def test_handle_candle_persists_candle_and_technical_features(db_session):
    await _seed_calendar(db_session, num_days=1)
    await _seed_strategy_parameters(db_session)

    provider = FakeDataProvider()
    loop = _make_loop(provider=provider, redis_client=FakeRedis(), tracked_symbols=["AAA"])
    await loop.start()

    sessions = generate_symbol_history(symbol="AAA", num_days=1, seed=1)
    for candle in sessions[0]:
        await loop.handle_candle(candle)

    from app.models.candles import Candle5m
    from app.models.technical_features import TechnicalFeatures

    candle_count = (await db_session.execute(select(Candle5m).where(Candle5m.symbol == "AAA"))).scalars().all()
    feature_count = (
        await db_session.execute(select(TechnicalFeatures).where(TechnicalFeatures.symbol == "AAA"))
    ).scalars().all()

    assert len(candle_count) == BARS_PER_SESSION
    # Only the latest row is (re)persisted per candle -- one feature row per bar, same count.
    assert len(feature_count) == BARS_PER_SESSION


async def test_market_context_published_once_both_context_symbols_warm_up(db_session):
    num_days = 3
    await _seed_calendar(db_session, num_days=num_days)
    await _seed_strategy_parameters(db_session)

    provider = FakeDataProvider()
    provider.market_context = None
    from app.data_providers.schemas import MarketContextData
    from app.models.enums import DataStatus

    provider.market_context = MarketContextData(
        timestamp=BASE_OPEN, spy_price=Decimal(450), qqq_price=Decimal(380), vix_value=Decimal(15),
        source="TEST", data_status=DataStatus.VALID,
    )

    redis_client = FakeRedis()
    loop = _make_loop(provider=provider, redis_client=redis_client, tracked_symbols=[])
    await loop.start()

    spy_sessions = generate_symbol_history(symbol="SPY", num_days=num_days, seed=11)
    qqq_sessions = generate_symbol_history(symbol="QQQ", num_days=num_days, seed=12)

    from app.core.market_context_cache import get_cached_market_context

    with TestClient(app) as client, client.websocket_connect("/ws/live-updates") as websocket:
        for day_index in range(num_days):
            for spy_candle, qqq_candle in zip(spy_sessions[day_index], qqq_sessions[day_index], strict=True):
                await loop.handle_candle(spy_candle)
                await loop.handle_candle(qqq_candle)

        cached = await get_cached_market_context(redis_client)
        assert cached is not None
        assert cached["overall_market_regime"] in {r.value for r in OverallMarketRegime}

        # At least one MARKET_CONTEXT_UPDATE message must have been broadcast
        # by the time both symbols warmed up.
        message = websocket.receive_json()
        assert message["type"] == "MARKET_CONTEXT_UPDATE"


async def test_full_lifecycle_creates_entry_signal_with_score_and_risk_plan(db_session):
    """The Phase 7 Replay harness already proved seed=1/60-days produces
    real CALL ENTRY signals under a SUPPORTIVE regime
    (docs/RULE_SANITY_CHECK_RESULTS_V0.1.md) — this test drives the exact
    same synthetic history through the live loop's `handle_candle` instead,
    proving the loop persists everything a real ENTRY needs: Signal scores,
    a RiskPlan, a SignalSnapshot, and a delivered alert."""
    num_days = 30  # covers seed=1's first known CALL ENTRY at day_index=27
    await _seed_calendar(db_session, num_days=num_days)
    await _seed_strategy_parameters(db_session)
    db_session.add(PortfolioRiskConfig(account_size=Decimal(50000), risk_percent=Decimal(1)))
    await db_session.commit()

    provider = FakeDataProvider()
    _seed_option_chain(provider, "ZZZ")

    redis_client = FakeRedis()
    loop = _make_loop(provider=provider, redis_client=redis_client, tracked_symbols=["ZZZ"])
    await loop.start()
    # Bypasses the real SPY/QQQ-derived market context (covered by its own
    # test above) so this test's only variable is the tracked symbol's
    # own breakout behavior, exactly like app/replay/harness.py's tests
    # take overall_market_regime as a direct input.
    loop._latest_overall_regime = OverallMarketRegime.SUPPORTIVE  # noqa: SLF001

    sessions = generate_symbol_history(symbol="ZZZ", num_days=num_days, seed=1)

    with TestClient(app) as client, client.websocket_connect("/ws/live-updates"):
        for day_index in range(num_days):  # every day, including the RVOL-baseline warm-up
            for candle in sessions[day_index]:
                await loop.handle_candle(candle)

    entry_signals = await _get_call_entry_signals(db_session, "ZZZ")
    assert len(entry_signals) >= 1

    signal = entry_signals[0]
    assert signal.final_score is not None
    assert Decimal(0) <= signal.final_score <= Decimal(100)
    assert signal.contract_score is not None
    assert signal.setup_score is not None
    assert signal.conditions is not None
    assert signal.hard_gates is not None
    assert signal.hard_gates["timing"] == "PASS"

    risk_plan = await db_session.get(RiskPlan, signal.signal_id)
    assert risk_plan is not None
    assert risk_plan.r_multiple == Decimal(2)

    snapshot = await db_session.get(SignalSnapshot, signal.signal_id)
    assert snapshot is not None
    assert snapshot.contract_id == "ZZZ_C1"

    notifications = (
        await db_session.execute(select(NotificationLog).where(NotificationLog.signal_id == signal.signal_id))
    ).scalars().all()
    assert len(notifications) >= 1


async def test_last_data_update_timestamp_is_advanced_for_the_circuit_breaker(db_session):
    await _seed_calendar(db_session, num_days=1)
    await _seed_strategy_parameters(db_session)

    class _FakeAppState:
        last_data_update_at = None

    state = _FakeAppState()
    provider = FakeDataProvider()
    loop = _make_loop(provider=provider, redis_client=FakeRedis(), tracked_symbols=["AAA"], app_state=state)
    await loop.start()

    assert state.last_data_update_at is None
    sessions = generate_symbol_history(symbol="AAA", num_days=1, seed=1)
    await loop.handle_candle(sessions[0][0])

    assert state.last_data_update_at is not None
    assert (datetime.now(UTC) - state.last_data_update_at) < timedelta(seconds=30)


async def test_portfolio_risk_breach_forces_zero_recommended_quantity(db_session):
    num_days = 30  # covers seed=1's first known CALL ENTRY at day_index=27
    await _seed_calendar(db_session, num_days=num_days)
    await _seed_strategy_parameters(db_session)
    # max_concurrent_signals=0 -- the very first ENTRY already breaches it.
    db_session.add(
        PortfolioRiskConfig(account_size=Decimal(50000), risk_percent=Decimal(1), max_concurrent_signals=0)
    )
    await db_session.commit()

    provider = FakeDataProvider()
    _seed_option_chain(provider, "ZZZ")

    loop = _make_loop(provider=provider, redis_client=FakeRedis(), tracked_symbols=["ZZZ"])
    await loop.start()
    loop._latest_overall_regime = OverallMarketRegime.SUPPORTIVE  # noqa: SLF001

    sessions = generate_symbol_history(symbol="ZZZ", num_days=num_days, seed=1)

    with TestClient(app) as client, client.websocket_connect("/ws/live-updates"):
        for day_index in range(num_days):  # every day, including the RVOL-baseline warm-up
            for candle in sessions[day_index]:
                await loop.handle_candle(candle)

    entry_signals = await _get_call_entry_signals(db_session, "ZZZ")
    assert len(entry_signals) >= 1

    risk_plan = await db_session.get(RiskPlan, entry_signals[0].signal_id)
    assert risk_plan is not None
    assert risk_plan.recommended_quantity == 0
    assert entry_signals[0].hard_gates["risk"] == "FAIL"
