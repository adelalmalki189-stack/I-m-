from __future__ import annotations

from decimal import Decimal

from app.engines.feature_engine.rvol import compute_rvol_for_session
from tests.engines.factories import make_candle


def _session_with_volume(volume: int, n: int = 3) -> list:
    return [make_candle(index=i, open_=100, high=101, low=99, close=100, volume=volume) for i in range(n)]


def test_rvol_is_none_during_warmup_below_min_valid_days():
    current = _session_with_volume(2000)
    baseline = [_session_with_volume(1000) for _ in range(5)]  # only 5 days, need 20
    result = compute_rvol_for_session(current, baseline, min_valid_days=20)
    assert all(v is None for v in result)


def test_rvol_computed_once_enough_valid_days():
    current = _session_with_volume(2000)
    baseline = [_session_with_volume(1000) for _ in range(20)]
    result = compute_rvol_for_session(current, baseline, min_valid_days=20)
    assert result == [Decimal(2)] * 3


def test_rvol_zero_baseline_is_none_not_infinite():
    current = _session_with_volume(2000)
    baseline = [_session_with_volume(0) for _ in range(20)]
    result = compute_rvol_for_session(current, baseline, min_valid_days=20)
    assert all(v is None for v in result)


def test_rvol_only_uses_days_that_have_a_candle_at_that_bucket():
    # A baseline day shorter than the current session (e.g. an early close
    # that slipped through filtering) must not count toward that bucket.
    current = _session_with_volume(2000, n=5)
    full_days = [_session_with_volume(1000, n=5) for _ in range(19)]
    short_day = _session_with_volume(1000, n=3)  # missing buckets 3, 4
    baseline = [*full_days, short_day]

    result = compute_rvol_for_session(current, baseline, min_valid_days=20)
    # Buckets 0-2 have 20 valid baseline days; buckets 3-4 only have 19.
    assert result[0] == Decimal(2)
    assert result[3] is None
    assert result[4] is None


def test_rvol_excludes_days_the_caller_did_not_include():
    # docs/CALCULATION_SPEC_V0.1.md §3.2 excludes half-days/event days —
    # this module has no calendar knowledge, so it trusts whatever the
    # caller passes in as "valid". Demonstrate that omitting a day from
    # `baseline_sessions` changes the result, proving exclusion works by
    # simply not including it.
    current = _session_with_volume(2000, n=1)
    with_20_valid_days = [_session_with_volume(1000, n=1) for _ in range(20)]
    result_full = compute_rvol_for_session(current, with_20_valid_days, min_valid_days=20)

    without_one_day = with_20_valid_days[:-1]  # simulate an excluded event day
    result_excluded = compute_rvol_for_session(current, without_one_day, min_valid_days=20)

    assert result_full == [Decimal(2)]
    assert result_excluded == [None]  # now below min_valid_days
