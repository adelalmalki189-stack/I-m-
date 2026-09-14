from __future__ import annotations

from decimal import Decimal

from app.engines.feature_engine.structure import build_swing_snapshots, find_confirmed_swings
from app.models.enums import MarketStructureState
from tests.engines.factories import make_candle


def _candle(i: int, high: float, low: float) -> object:
    return make_candle(index=i, open_=(high + low) / 2, high=high, low=low, close=(high + low) / 2)


def test_swing_high_requires_k_candles_on_both_sides():
    # A clear local peak at index 2, k=2 needs indices 0,1 and 3,4 too.
    candles = [
        _candle(0, 100, 95),
        _candle(1, 102, 98),
        _candle(2, 110, 100),  # the peak
        _candle(3, 103, 99),
        _candle(4, 101, 97),
    ]
    swings = find_confirmed_swings(candles, k=2)
    highs = [s for s in swings if s.kind == "HIGH"]
    assert len(highs) == 1
    assert highs[0].index == 2
    assert highs[0].confirmed_at_index == 4  # not confirmable before index 4


def test_no_swing_detected_at_edges_with_insufficient_window():
    # A peak at index 0 or at the very end can never be confirmed with k=2
    # candles on both sides — must not be reported as a swing at all.
    candles = [_candle(i, 100 - i, 90 - i) for i in range(3)]  # monotonic, no interior peak
    swings = find_confirmed_swings(candles, k=2)
    assert swings == []


def test_market_structure_none_until_two_highs_and_two_lows_confirmed():
    flat = [_candle(i, 100, 99) for i in range(6)]
    swings = find_confirmed_swings(flat, k=2)
    snapshots = build_swing_snapshots(flat, swings)
    assert all(s.market_structure is None for s in snapshots)


def test_market_structure_bullish_on_higher_high_and_higher_low():
    # Verified turning points (flat candles, high=low=close=price):
    # trough@2=80, peak@6=120, trough@10=85 (higher low), peak@14=130
    # (higher high) — each confirmed k=2 candles later.
    prices = [100, 90, 80, 90, 100, 110, 120, 110, 100, 90, 85, 90, 100, 110, 130, 120, 110]
    candles = [_candle(i, p, p) for i, p in enumerate(prices)]
    swings = find_confirmed_swings(candles, k=2)
    snapshots = build_swing_snapshots(candles, swings)

    # By the last candle, both a higher swing high and higher swing low
    # should have been confirmed, yielding BULLISH.
    assert snapshots[-1].market_structure == MarketStructureState.BULLISH


def test_swing_snapshot_never_reveals_a_swing_before_its_confirmation_index():
    candles = [
        _candle(0, 100, 95),
        _candle(1, 102, 98),
        _candle(2, 110, 100),
        _candle(3, 103, 99),
        _candle(4, 101, 97),
    ]
    swings = find_confirmed_swings(candles, k=2)
    snapshots = build_swing_snapshots(candles, swings)

    # The swing at index 2 confirms at index 4 — snapshot at index 3 must
    # not know about it yet.
    assert snapshots[3].swing_high is None
    assert snapshots[4].swing_high == Decimal(110)
