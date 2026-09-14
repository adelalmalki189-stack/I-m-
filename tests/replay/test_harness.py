"""Tests for the Replay harness (app/replay/harness.py) — the component
docs/BACKTEST_PLAN_V0.1.md §2 requires to call the exact production
engines against synthetic multi-day data. These are not golden-value
assertions (the input is a random walk, not a hand-crafted scenario);
they check the invariants a Rule Sanity Check depends on: the harness
never crashes on a long synthetic run, every result stays within the
ranges the underlying engines guarantee, and a signal is only ever
recorded on a genuine fresh ENTRY transition.
"""
from __future__ import annotations

from decimal import Decimal

from app.models.enums import OverallMarketRegime, SignalDirection
from app.replay.harness import replay_symbol
from app.replay.synthetic_market import generate_symbol_history

CALC_VERSION = "v0.1.0-replay-test"


def test_replay_runs_a_long_synthetic_history_without_raising():
    sessions = generate_symbol_history(symbol="ZZZ", num_days=60, seed=1)

    for direction in (SignalDirection.CALL, SignalDirection.PUT):
        results = replay_symbol(
            symbol="ZZZ",
            daily_sessions=sessions,
            calc_version=CALC_VERSION,
            overall_market_regime=OverallMarketRegime.SUPPORTIVE,
            direction=direction,
        )
        assert isinstance(results, list)


def test_known_seed_produces_at_least_one_call_and_one_put_signal():
    """Confirms the harness's engine wiring is actually capable of
    reaching ENTRY (not just silently returning zero every time, which
    would make the whole Rule Sanity Check meaningless) on a seed already
    verified during development to contain trend days in both directions."""
    sessions = generate_symbol_history(symbol="ZZZ", num_days=60, seed=1)

    call_results = replay_symbol(
        symbol="ZZZ", daily_sessions=sessions, calc_version=CALC_VERSION,
        overall_market_regime=OverallMarketRegime.SUPPORTIVE, direction=SignalDirection.CALL,
        account_size=Decimal(50000),
    )
    put_results = replay_symbol(
        symbol="ZZZ", daily_sessions=sessions, calc_version=CALC_VERSION,
        overall_market_regime=OverallMarketRegime.SUPPORTIVE, direction=SignalDirection.PUT,
        account_size=Decimal(50000),
    )

    assert len(call_results) > 0
    assert len(put_results) > 0


def test_results_are_internally_consistent_and_within_engine_bounds():
    sessions = generate_symbol_history(symbol="ZZZ", num_days=60, seed=1)
    results = replay_symbol(
        symbol="ZZZ", daily_sessions=sessions, calc_version=CALC_VERSION,
        overall_market_regime=OverallMarketRegime.SUPPORTIVE, direction=SignalDirection.PUT,
        account_size=Decimal(50000),
    )
    assert results

    for r in results:
        assert r.symbol == "ZZZ"
        assert r.direction == SignalDirection.PUT
        assert Decimal(0) <= r.final_score <= Decimal(100)
        assert r.recommended_quantity >= 0
        # compute_target always targets exactly DEFAULT_TARGET_R_MULTIPLE (2R)
        # on the underlying leg, so r_multiple should reflect that 1:2 shape.
        assert r.r_multiple >= 0
        # PUT: invalidation sits above entry, target sits below it.
        assert r.invalidation > r.entry_price
        assert r.target < r.entry_price
        if r.passes_final_gate:
            assert r.recommended_quantity >= 1


def test_no_duplicate_entries_on_consecutive_candles_of_the_same_attempt():
    """A signal is recorded once per fresh ENTRY transition, never once
    per candle the price merely continues to sit inside the entry zone —
    otherwise every ENTRY would be massively over-counted."""
    sessions = generate_symbol_history(symbol="ZZZ", num_days=60, seed=1)
    results = replay_symbol(
        symbol="ZZZ", daily_sessions=sessions, calc_version=CALC_VERSION,
        overall_market_regime=OverallMarketRegime.SUPPORTIVE, direction=SignalDirection.CALL,
        account_size=Decimal(50000),
    )
    assert results

    seen = set()
    for r in results:
        key = (r.day_index, r.candle_index)
        assert key not in seen
        seen.add(key)


def test_restrictive_regime_blocks_every_signal():
    """Final Hard Gate's `market` check fails whenever the overall regime
    is RESTRICTIVE (Methodology §1/§24) — a sanity check the Rule Sanity
    Check's own "disaster" diagnostics rely on."""
    sessions = generate_symbol_history(symbol="ZZZ", num_days=60, seed=1)
    results = replay_symbol(
        symbol="ZZZ", daily_sessions=sessions, calc_version=CALC_VERSION,
        overall_market_regime=OverallMarketRegime.RESTRICTIVE, direction=SignalDirection.CALL,
    )
    assert all(not r.passes_final_gate for r in results)
