from __future__ import annotations

from decimal import Decimal

import pytest

from app.engines.feature_engine.vwap import compute_session_vwap
from tests.engines.factories import make_candle


def test_single_candle_vwap_equals_typical_price():
    candle = make_candle(index=0, open_=100, high=102, low=98, close=100, volume=1000)
    result = compute_session_vwap([candle])
    expected_typical_price = (Decimal(102) + Decimal(98) + Decimal(100)) / Decimal(3)
    assert result == [expected_typical_price]


def test_vwap_accumulates_across_candles():
    candles = [
        make_candle(index=0, open_=100, high=101, low=99, close=100, volume=1000),
        make_candle(index=1, open_=100, high=103, low=99, close=102, volume=3000),
    ]
    result = compute_session_vwap(candles)

    tp1 = (Decimal(101) + Decimal(99) + Decimal(100)) / Decimal(3)
    tp2 = (Decimal(103) + Decimal(99) + Decimal(102)) / Decimal(3)
    expected_vwap_2 = (tp1 * 1000 + tp2 * 3000) / Decimal(4000)

    assert result[0] == tp1
    assert result[1] == expected_vwap_2


def test_vwap_resets_are_the_callers_responsibility_zero_volume_raises():
    # A zero-volume first candle would divide by zero — must raise, not
    # silently return an undefined VWAP.
    candle = make_candle(index=0, open_=100, high=100, low=100, close=100, volume=0)
    with pytest.raises(ValueError):
        compute_session_vwap([candle])


def test_vwap_does_not_leak_across_two_separately_passed_sessions():
    # Session-anchoring (docs/CALCULATION_SPEC_V0.1.md §1) means calling the
    # function again for a new session must not carry over the old
    # cumulative totals — verified here by checking two independent calls
    # give the same first-candle result regardless of what happened "before".
    day1 = [make_candle(index=0, open_=100, high=110, low=90, close=105, volume=5000)]
    day2 = [make_candle(index=0, open_=200, high=210, low=190, close=205, volume=7000)]

    vwap_day1 = compute_session_vwap(day1)
    vwap_day2 = compute_session_vwap(day2)

    expected_day2 = (Decimal(210) + Decimal(190) + Decimal(205)) / Decimal(3)
    assert vwap_day2 == [expected_day2]
    assert vwap_day1 != vwap_day2
