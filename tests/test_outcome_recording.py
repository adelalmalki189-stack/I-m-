"""Phase 6 completion criterion — outcome half of it
(docs/IMPLEMENTATION_PLAN_V0.1.md): "بيانات outcome تتراكم تلقائياً بدون
تدخل يدوي." Proves record_outcomes_for_signal only creates rows for
checkpoints that have actually elapsed, never duplicates a checkpoint on
a re-run, and fills in later checkpoints once more time has passed —
exactly what a periodic script (app/scripts/run_outcome_tracker.py) would
do unattended.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models.candles import Candle5m
from app.models.enums import SignalDirection, SignalStatus
from app.models.instruments import Instrument
from app.models.outcomes import Outcome
from app.models.signal_snapshots import SignalSnapshot
from app.models.signals import Signal
from app.models.strategy_parameters import StrategyParameters
from app.scripts.run_outcome_tracker import record_outcomes_for_signal

CALC_VERSION = "v0.1.0-outcome-tracker"
ENTRY_TIME = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)


async def _seed_signal_with_candles(db_session) -> Signal:
    db_session.add(Instrument(symbol="NVDA", exchange="SMART", is_active=True))
    db_session.add(StrategyParameters(version=CALC_VERSION, parameters={}, effective_from=ENTRY_TIME))
    await db_session.flush()

    signal = Signal(
        timestamp=ENTRY_TIME, symbol="NVDA", direction=SignalDirection.CALL,
        status=SignalStatus.ENTRY, calc_version=CALC_VERSION,
    )
    db_session.add(signal)
    await db_session.flush()

    db_session.add(
        SignalSnapshot(
            signal_id=signal.signal_id, underlying_price=Decimal(100), vwap=Decimal(99),
            calc_version=CALC_VERSION,
        )
    )

    # Candles every 5 minutes from entry through +60 minutes (13 bars),
    # with a clear high-water mark at +15 (110) and a dip at +45 (95).
    prices = {
        0: 101, 5: 103, 10: 106, 15: 110, 20: 108, 25: 104, 30: 102,
        35: 100, 40: 98, 45: 95, 50: 97, 55: 99, 60: 101,
    }
    for offset_min, price in prices.items():
        ts = ENTRY_TIME + timedelta(minutes=offset_min)
        db_session.add(
            Candle5m(
                symbol="NVDA", timestamp=ts, open=Decimal(price), high=Decimal(price + 1),
                low=Decimal(price - 1), close=Decimal(price), volume=1000, vwap=Decimal(price),
                is_complete=True,
            )
        )
    await db_session.commit()
    return signal


async def test_only_elapsed_checkpoints_are_recorded(db_session):
    signal = await _seed_signal_with_candles(db_session)

    created = await record_outcomes_for_signal(db_session, signal, now=ENTRY_TIME + timedelta(minutes=35))

    assert {o.offset_minutes for o in created} == {5, 10, 15, 30}
    # The +60 checkpoint hasn't elapsed relative to `now` yet.
    outcome_60 = await db_session.get(Outcome, {"signal_id": signal.signal_id, "offset_minutes": 60})
    assert outcome_60 is None


async def test_rerun_does_not_duplicate_existing_checkpoints(db_session):
    signal = await _seed_signal_with_candles(db_session)

    first_run = await record_outcomes_for_signal(db_session, signal, now=ENTRY_TIME + timedelta(minutes=35))
    assert len(first_run) == 4

    second_run = await record_outcomes_for_signal(db_session, signal, now=ENTRY_TIME + timedelta(minutes=35))
    assert second_run == []

    all_outcomes = (
        await db_session.execute(select(Outcome).where(Outcome.signal_id == signal.signal_id))
    ).scalars().all()
    assert len(all_outcomes) == 4  # still just the original four, no duplicates


async def test_later_run_fills_in_newly_elapsed_checkpoint(db_session):
    signal = await _seed_signal_with_candles(db_session)

    await record_outcomes_for_signal(db_session, signal, now=ENTRY_TIME + timedelta(minutes=35))
    later_run = await record_outcomes_for_signal(db_session, signal, now=ENTRY_TIME + timedelta(minutes=65))

    assert {o.offset_minutes for o in later_run} == {60}

    outcome_15 = await db_session.get(Outcome, {"signal_id": signal.signal_id, "offset_minutes": 15})
    assert outcome_15 is not None
    # High-water mark by +15 is 110+1=111 -> MFE = 111 - entry(100) = 11
    assert outcome_15.mfe == Decimal(11)
