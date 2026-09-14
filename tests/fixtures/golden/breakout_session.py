"""Phase 2's Golden Scenario (docs/IMPLEMENTATION_PLAN_V0.1.md Phase 2
completion criterion): a synthetic-but-internally-consistent CALL breakout
session, run through the real Feature Engine -> Setup Engine -> Timing
Engine chain (docs/TESTING_SPEC_V0.1.md §5).

Not real market data, for the same reason as
tests/fixtures/golden/simple_uptrend_session.py — there is no historical
backfill wired up yet. The zigzag in `PRICES` (verified in
test_market_structure_bullish_on_higher_high_and_higher_low, Phase 1) is
reused here specifically because it is already a known-good BULLISH
Market Structure fixture, then extended with a genuine breakout leg.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.data_providers.schemas import Candle
from app.models.enums import DataStatus

SYMBOL = "GOLDBRK"
SESSION_OPEN = datetime(2026, 3, 2, 9, 30, tzinfo=UTC)  # a Monday
BASELINE_DAY_COUNT = 20

# Verified zigzag (trough@2=80, peak@6=120, trough@10=85, peak@14=130) that
# confirms BULLISH market structure by index 16 (Phase 1's
# test_structure.py), followed by a consolidation (17-19) and a breakout
# candle (20) with a volume spike, then two more candles to observe the
# post-entry path.
PRICES = [
    100, 90, 80, 90, 100, 110, 120, 110, 100, 90, 85, 90, 100, 110, 130, 120, 110, 108, 106, 108,
    145, 143, 141, 125,
]
VOLUMES = [1000] * 20 + [3000, 1500, 1500, 1200]
BREAKOUT_INDEX = 20  # the candle that closes above the confirmed swing high (130)
PULLBACK_INVALIDATION_INDEX = 23  # closes back below the frozen breakout level -> INVALIDATED


def _candle(index: int, price: float, volume: int, *, session_open: datetime = SESSION_OPEN) -> Candle:
    p = Decimal(str(price))
    return Candle(
        symbol=SYMBOL,
        timestamp=session_open + timedelta(minutes=5 * index),
        open=p,
        high=p,
        low=p,
        close=p,
        volume=volume,
        vwap=p,
        is_complete=True,
        source="GOLDEN_FIXTURE",
        data_status=DataStatus.VALID,
    )


def build_current_session() -> list[Candle]:
    return [_candle(i, price, vol) for i, (price, vol) in enumerate(zip(PRICES, VOLUMES, strict=True))]


def build_baseline_sessions(n: int = BASELINE_DAY_COUNT) -> list[list[Candle]]:
    """Flat 1000-volume baseline at every bucket, same trick as
    simple_uptrend_session.py — makes the RVOL spike at BREAKOUT_INDEX
    exactly 3000/1000 = 3.0, comfortably above the 1.30 threshold."""
    sessions = []
    for d in range(1, n + 1):
        session_open = SESSION_OPEN - timedelta(days=1 + d)
        sessions.append([_candle(i, 100, 1000, session_open=session_open) for i in range(len(PRICES))])
    return sessions
