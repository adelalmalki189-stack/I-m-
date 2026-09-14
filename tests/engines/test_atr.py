from __future__ import annotations

from decimal import Decimal

from app.engines.feature_engine.atr import compute_atr, compute_true_range
from tests.engines.factories import make_candle


def test_first_candle_true_range_is_high_minus_low():
    candle = make_candle(index=0, open_=100, high=105, low=95, close=102)
    result = compute_true_range([candle])
    assert result == [Decimal(10)]


def test_true_range_captures_gap_beyond_high_low():
    prev = make_candle(index=0, open_=100, high=101, low=99, close=100)
    # Gapped up hard: high-low is only 2, but the gap from prior close is 19
    gapped = make_candle(index=1, open_=118, high=120, low=118, close=119)
    result = compute_true_range([prev, gapped])
    assert result[1] == Decimal(20)  # |120 - 100|


def test_atr_none_before_warmup_period():
    candles = [make_candle(index=i, open_=100, high=101, low=99, close=100) for i in range(5)]
    result = compute_atr(candles, period=14)
    assert all(v is None for v in result)


def test_atr_seed_equals_average_true_range_over_period():
    candles = [make_candle(index=i, open_=100, high=102, low=98, close=100) for i in range(3)]
    result = compute_atr(candles, period=3)
    assert result[2] == Decimal(4)  # every TR is high-low=4, SMA seed = 4
