"""Phase 2 completion criterion (docs/IMPLEMENTATION_PLAN_V0.1.md):

"signals تُنشأ بحالات صحيحة على بيانات Replay تاريخية، وMarket Context
(SPY/QQQ/VIX regime) يُحسب ويُدمج كشرط سياقي."

Runs the real chain — Feature Engine -> Market Context Engine -> Setup
Engine -> Timing Engine — over the breakout_session golden fixture, then
persists the resulting Signal + SignalSnapshot through the ORM.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.engines.feature_engine.pipeline import compute_technical_features_for_session
from app.engines.market_context_engine import (
    classify_market_regime,
    classify_vix_regime,
    combine_overall_regime,
)
from app.engines.setup_engine import (
    compute_entry_zone,
    compute_invalidation,
    compute_target,
    evaluate_setup_conditions,
)
from app.engines.timing_engine import determine_status
from app.models.enums import MarketStructureState, SignalDirection, SignalStatus, TrendSlope
from app.models.instruments import Instrument
from app.models.signal_snapshots import SignalSnapshot
from app.models.signals import Signal
from app.models.strategy_parameters import StrategyParameters
from tests.fixtures.golden.breakout_session import (
    BREAKOUT_INDEX,
    PULLBACK_INVALIDATION_INDEX,
    build_baseline_sessions,
    build_current_session,
)

CALC_VERSION = "v0.1.0-golden-breakout"
FROZEN_RESISTANCE_LEVEL = Decimal(130)


def _compute_rows():
    return compute_technical_features_for_session(
        build_current_session(),
        previous_session_candles=None,
        baseline_sessions_for_rvol=build_baseline_sessions(),
        calc_version=CALC_VERSION,
    )


def _supportive_market_regime():
    """A directly-specified bullish SPY/QQQ + normal VIX reading — Market
    Context Engine's own regime math is already unit-tested in
    test_market_context.py, so this exercises its real public functions
    without needing a second full multi-day Feature Engine run for SPY/QQQ."""
    spy_regime = classify_market_regime(
        price=Decimal(105), vwap=Decimal(100), ema20=Decimal(102), ema50=Decimal(100),
        ema20_slope=TrendSlope.POSITIVE, market_structure=MarketStructureState.BULLISH,
    )
    qqq_regime = classify_market_regime(
        price=Decimal(205), vwap=Decimal(200), ema20=Decimal(202), ema50=Decimal(200),
        ema20_slope=TrendSlope.POSITIVE, market_structure=MarketStructureState.BULLISH,
    )
    vix_regime = classify_vix_regime(Decimal(15))
    return combine_overall_regime(spy_regime=spy_regime, qqq_regime=qqq_regime, vix_regime=vix_regime)


def test_breakout_session_produces_expected_status_sequence():
    candles = build_current_session()
    rows = _compute_rows()
    overall_market_regime = _supportive_market_regime()

    # The resistance level and its ATR are frozen at the breakout candle —
    # a live signal doesn't re-select a new resistance level on every
    # later candle, it tracks the one it was created against.
    atr_frozen = rows[BREAKOUT_INDEX].atr14
    assert atr_frozen is not None
    zone = compute_entry_zone(level=FROZEN_RESISTANCE_LEVEL, atr14=atr_frozen, direction=SignalDirection.CALL)

    statuses: list[SignalStatus] = []
    previous_status: SignalStatus | None = None
    for i in range(BREAKOUT_INDEX, PULLBACK_INVALIDATION_INDEX + 1):
        row = rows[i]
        conditions = evaluate_setup_conditions(
            direction=SignalDirection.CALL,
            price=candles[i].close,
            vwap=row.vwap,
            close=candles[i].close,
            market_structure=row.market_structure,
            level=FROZEN_RESISTANCE_LEVEL,
            rvol=row.rvol,
            atr14=atr_frozen,
            overall_market_regime=overall_market_regime,
            event_severity=None,
        )
        status = determine_status(
            direction=SignalDirection.CALL,
            is_pre_breakout_ok=conditions.pre_breakout_ok,
            breakout_confirmed=conditions.breakout_confirmed,
            breakout_level=FROZEN_RESISTANCE_LEVEL,
            close=candles[i].close,
            entry_zone=zone,
            previous_status=previous_status,
        )
        statuses.append(status)
        previous_status = status

    # index 20 (145) and 21 (143): breakout confirmed but past max_extension
    # -> WAIT (Avoid Chasing, docs/CALCULATION_SPEC_V0.1.md §7).
    # index 22 (141): pulled back into the acceptable zone -> ENTRY.
    # index 23 (125): closes back below the frozen breakout level -> INVALIDATED.
    assert statuses == [
        SignalStatus.WAIT,
        SignalStatus.WAIT,
        SignalStatus.ENTRY,
        SignalStatus.INVALIDATED,
    ]


async def test_breakout_signal_persists_through_the_orm(db_session):
    candles = build_current_session()
    rows = _compute_rows()
    overall_market_regime = _supportive_market_regime()

    breakout_row = rows[BREAKOUT_INDEX]
    atr_frozen = breakout_row.atr14
    assert atr_frozen is not None

    invalidation = compute_invalidation(
        level=FROZEN_RESISTANCE_LEVEL,
        atr14=atr_frozen,
        swing_extreme=breakout_row.swing_low,
        direction=SignalDirection.CALL,
    )
    entry_price = candles[22].close  # the candle where status became ENTRY
    target = compute_target(entry_price=entry_price, invalidation=invalidation, direction=SignalDirection.CALL)
    zone = compute_entry_zone(level=FROZEN_RESISTANCE_LEVEL, atr14=atr_frozen, direction=SignalDirection.CALL)

    db_session.add(Instrument(symbol=candles[0].symbol, exchange="SMART", is_active=True))
    db_session.add(
        StrategyParameters(
            version=CALC_VERSION,
            parameters={"note": "golden breakout fixture"},
            effective_from=candles[0].timestamp,
        )
    )
    await db_session.flush()

    signal = Signal(
        timestamp=candles[BREAKOUT_INDEX].timestamp,
        symbol=candles[0].symbol,
        direction=SignalDirection.CALL,
        status=SignalStatus.INVALIDATED,  # final observed status from the sequence test
        entry_zone_low=zone.acceptable_min,
        entry_zone_high=zone.acceptable_max,
        invalidation=invalidation,
        target=target,
        calc_version=CALC_VERSION,
    )
    db_session.add(signal)
    await db_session.flush()

    db_session.add(
        SignalSnapshot(
            signal_id=signal.signal_id,
            underlying_price=candles[BREAKOUT_INDEX].close,
            vwap=breakout_row.vwap,
            ema20=breakout_row.ema20,
            ema50=breakout_row.ema50,  # still None at warm-up — nullable, per the schema fix above
            rvol=breakout_row.rvol,
            spy_state=overall_market_regime.value,
            calc_version=CALC_VERSION,
        )
    )
    await db_session.commit()

    result = await db_session.execute(select(Signal).where(Signal.symbol == candles[0].symbol))
    persisted = result.scalar_one()
    assert persisted.status == SignalStatus.INVALIDATED
    assert persisted.invalidation == invalidation

    snapshot_result = await db_session.execute(
        select(SignalSnapshot).where(SignalSnapshot.signal_id == signal.signal_id)
    )
    persisted_snapshot = snapshot_result.scalar_one()
    assert persisted_snapshot.ema50 is None
