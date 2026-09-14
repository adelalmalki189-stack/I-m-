"""Regression test for the cross-day EMA/ATR continuity gap found while
building Phase 7 (docs/CALCULATION_SPEC_V0.1.md §2: EMA must be
"مستمرة عبر الأيام"). Confirms both that the opt-in parameter actually
restores continuity, and that omitting it exactly reproduces the old
single-session behavior every prior phase's tests depend on.
"""
from __future__ import annotations

from app.engines.feature_engine.pipeline import compute_technical_features_for_session
from tests.engines.factories import make_flat_session


def test_continuity_parameter_carries_ema_seed_across_days():
    day1 = make_flat_session(n=25, price=100.0)
    day2 = make_flat_session(n=5, price=100.0)  # too short to seed EMA20 (needs 20) on its own

    without_continuity = compute_technical_features_for_session(
        day2, previous_session_candles=day1, baseline_sessions_for_rvol=[], calc_version="v1"
    )
    assert all(r.ema20 is None for r in without_continuity)  # day2 alone never reaches 20 bars

    with_continuity = compute_technical_features_for_session(
        day2, previous_session_candles=day1, baseline_sessions_for_rvol=[], calc_version="v1",
        prior_candles_for_continuity=day1,
    )
    # With day1's 25 bars carried forward, EMA20 is already seeded well
    # before day2 even starts, so every day2 row has a real value.
    assert all(r.ema20 is not None for r in with_continuity)


def test_continuity_parameter_carries_atr_across_days():
    day1 = make_flat_session(n=20, price=100.0)
    day2 = make_flat_session(n=5, price=100.0)  # too short to seed ATR14 (needs 14) on its own

    without_continuity = compute_technical_features_for_session(
        day2, previous_session_candles=day1, baseline_sessions_for_rvol=[], calc_version="v1"
    )
    assert all(r.atr14 is None for r in without_continuity)

    with_continuity = compute_technical_features_for_session(
        day2, previous_session_candles=day1, baseline_sessions_for_rvol=[], calc_version="v1",
        prior_candles_for_continuity=day1,
    )
    assert all(r.atr14 is not None for r in with_continuity)
