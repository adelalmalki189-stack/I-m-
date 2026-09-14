"""Unit tests for the synthetic multi-day generator (app/replay/synthetic_market.py)
used to drive the Rule Sanity Check (docs/BACKTEST_PLAN_V0.1.md).
"""
from __future__ import annotations

from decimal import Decimal

from app.replay.synthetic_market import BARS_PER_SESSION, generate_symbol_history


def test_same_seed_is_fully_deterministic():
    first = generate_symbol_history(symbol="AAA", num_days=10, seed=42)
    second = generate_symbol_history(symbol="AAA", num_days=10, seed=42)

    assert len(first) == len(second) == 10
    for day_first, day_second in zip(first, second, strict=True):
        assert day_first == day_second


def test_different_seeds_diverge():
    first = generate_symbol_history(symbol="AAA", num_days=10, seed=1)
    second = generate_symbol_history(symbol="AAA", num_days=10, seed=2)

    assert first != second


def test_every_session_has_the_expected_bar_count():
    sessions = generate_symbol_history(symbol="AAA", num_days=5, seed=7)
    assert all(len(session) == BARS_PER_SESSION for session in sessions)


def test_timestamps_are_strictly_increasing_within_and_across_sessions():
    sessions = generate_symbol_history(symbol="AAA", num_days=5, seed=7)
    all_candles = [c for session in sessions for c in session]
    timestamps = [c.timestamp for c in all_candles]
    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) == len(timestamps)  # no duplicate bar


def test_prices_and_volumes_stay_positive_and_ohlc_is_internally_consistent():
    sessions = generate_symbol_history(symbol="AAA", num_days=15, seed=99)
    for session in sessions:
        for candle in session:
            assert candle.open > 0
            assert candle.high > 0
            assert candle.low > 0
            assert candle.close > 0
            assert candle.volume > 0
            assert candle.high >= max(candle.open, candle.close)
            assert candle.low <= min(candle.open, candle.close)


def test_each_session_opens_at_the_prior_sessions_close():
    sessions = generate_symbol_history(symbol="AAA", num_days=5, seed=3)
    for prev_session, session in zip(sessions[:-1], sessions[1:], strict=True):
        assert session[0].open == prev_session[-1].close


def test_start_price_is_respected():
    sessions = generate_symbol_history(symbol="AAA", num_days=1, seed=3, start_price=Decimal(250))
    assert sessions[0][0].open == Decimal(250)
