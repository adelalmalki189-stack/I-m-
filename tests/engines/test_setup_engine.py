from __future__ import annotations

from decimal import Decimal

import pytest

from app.engines.setup_engine import (
    compute_entry_zone,
    compute_invalidation,
    compute_target,
    evaluate_setup_conditions,
    is_breakout_confirmed,
    select_level,
)
from app.models.enums import EventSeverity, MarketStructureState, OverallMarketRegime, SignalDirection


def test_select_level_prefers_nearest_within_range():
    level = select_level(
        price=Decimal(100), swing_extreme=Decimal(103), prior_day_extreme=Decimal(101),
        opening_range_extreme=Decimal(108), atr14=Decimal(2),  # 1.5*ATR = 3
    )
    assert level == Decimal(101)  # nearest candidate within 1.5*ATR of price


def test_select_level_falls_back_to_swing_when_none_within_range():
    level = select_level(
        price=Decimal(100), swing_extreme=Decimal(150), prior_day_extreme=Decimal(160),
        opening_range_extreme=Decimal(170), atr14=Decimal(1),  # 1.5*ATR = 1.5, none close
    )
    assert level == Decimal(150)


def test_select_level_none_when_no_candidates():
    assert select_level(
        price=Decimal(100), swing_extreme=None, prior_day_extreme=None,
        opening_range_extreme=None, atr14=Decimal(1),
    ) is None


def test_breakout_confirmed_at_exact_minimum_rvol_threshold():
    # docs/TESTING_SPEC_V0.1.md §3: "كل شروط CALL محققة بالضبط على الحد
    # الأدنى (RVOL=1.30 تماماً) -> CALL Candidate"
    confirmed = is_breakout_confirmed(
        close=Decimal("100.30"), level=Decimal(100), rvol=Decimal("1.30"), atr14=Decimal(2),
        direction=SignalDirection.CALL,
    )
    assert confirmed is True


def test_breakout_rejected_below_min_distance():
    # distance = 0.05, min required = 0.10 * ATR14(2) = 0.20
    confirmed = is_breakout_confirmed(
        close=Decimal("100.05"), level=Decimal(100), rvol=Decimal(2), atr14=Decimal(2),
        direction=SignalDirection.CALL,
    )
    assert confirmed is False


def test_breakout_rejected_below_rvol_threshold():
    confirmed = is_breakout_confirmed(
        close=Decimal("101.00"), level=Decimal(100), rvol=Decimal("1.29"), atr14=Decimal(2),
        direction=SignalDirection.CALL,
    )
    assert confirmed is False


def test_breakout_rejected_on_missing_inputs():
    assert is_breakout_confirmed(
        close=Decimal(101), level=None, rvol=Decimal(2), atr14=Decimal(2), direction=SignalDirection.CALL
    ) is False
    assert is_breakout_confirmed(
        close=Decimal(101), level=Decimal(100), rvol=None, atr14=Decimal(2), direction=SignalDirection.CALL
    ) is False
    assert is_breakout_confirmed(
        close=Decimal(101), level=Decimal(100), rvol=Decimal(2), atr14=None, direction=SignalDirection.CALL
    ) is False


def test_entry_zone_call_direction():
    zone = compute_entry_zone(level=Decimal(100), atr14=Decimal(2), direction=SignalDirection.CALL)
    assert zone.ideal_low == Decimal(100)
    assert zone.ideal_high == Decimal(101)  # +0.5*ATR
    assert zone.max_extension == Decimal(102)  # +1.0*ATR
    assert zone.acceptable_min == Decimal(100)
    assert zone.acceptable_max == Decimal(102)


def test_entry_zone_put_direction_mirrors_below_level():
    zone = compute_entry_zone(level=Decimal(100), atr14=Decimal(2), direction=SignalDirection.PUT)
    assert zone.ideal_high == Decimal(100)
    assert zone.ideal_low == Decimal(99)
    assert zone.max_extension == Decimal(98)
    assert zone.acceptable_min == Decimal(98)
    assert zone.acceptable_max == Decimal(100)


def test_price_beyond_max_extension_is_outside_acceptable_zone():
    zone = compute_entry_zone(level=Decimal(100), atr14=Decimal(2), direction=SignalDirection.CALL)
    price_too_far = Decimal(103)
    assert not (zone.acceptable_min <= price_too_far <= zone.acceptable_max)


def test_invalidation_uses_the_lower_of_atr_buffer_and_swing_low():
    # docs/CALCULATION_SPEC_V0.1.md §8.1 uses min(...) for CALL: ATR buffer
    # candidate = 100 - 0.25*4 = 99; swing low = 99.5 -> min is 99 (the
    # wider/more conservative stop, not the closer one).
    invalidation = compute_invalidation(
        level=Decimal(100), atr14=Decimal(4), swing_extreme=Decimal("99.5"), direction=SignalDirection.CALL
    )
    assert invalidation == Decimal(99)


def test_invalidation_uses_swing_low_when_it_is_the_lower_value():
    invalidation = compute_invalidation(
        level=Decimal(100), atr14=Decimal(4), swing_extreme=Decimal("97.5"), direction=SignalDirection.CALL
    )
    assert invalidation == Decimal("97.5")


def test_invalidation_without_swing_uses_atr_buffer_only():
    invalidation = compute_invalidation(
        level=Decimal(100), atr14=Decimal(4), swing_extreme=None, direction=SignalDirection.CALL
    )
    assert invalidation == Decimal(99)


def test_target_is_two_r_by_default():
    target = compute_target(entry_price=Decimal(100), invalidation=Decimal(98), direction=SignalDirection.CALL)
    assert target == Decimal(104)  # risk=2, target = 100 + 2*2


def test_target_raises_on_non_positive_risk():
    with pytest.raises(ValueError):
        compute_target(entry_price=Decimal(100), invalidation=Decimal(101), direction=SignalDirection.CALL)


def test_setup_conditions_full_candidate_at_minimum_thresholds():
    conditions = evaluate_setup_conditions(
        direction=SignalDirection.CALL,
        price=Decimal("100.30"),
        vwap=Decimal(100),
        close=Decimal("100.30"),
        market_structure=MarketStructureState.BULLISH,
        level=Decimal(100),
        rvol=Decimal("1.30"),
        atr14=Decimal(2),
        overall_market_regime=OverallMarketRegime.SUPPORTIVE,
        event_severity=EventSeverity.LOW,
    )
    assert conditions.is_full_candidate is True


def test_setup_conditions_data_quality_gate_blocks_on_missing_rvol():
    # docs/TESTING_SPEC_V0.1.md §3: any STALE/INVALID input -> no trade,
    # regardless of how good everything else looks.
    conditions = evaluate_setup_conditions(
        direction=SignalDirection.CALL,
        price=Decimal("100.30"),
        vwap=Decimal(100),
        close=Decimal("100.30"),
        market_structure=MarketStructureState.BULLISH,
        level=Decimal(100),
        rvol=None,  # e.g. still in RVOL warm-up
        atr14=Decimal(2),
        overall_market_regime=OverallMarketRegime.SUPPORTIVE,
        event_severity=EventSeverity.LOW,
    )
    assert conditions.is_full_candidate is False
    assert conditions.breakout_confirmed is False
    assert conditions.rvol_confirmed is False


def test_setup_conditions_event_risk_gate_rejects_high_severity():
    conditions = evaluate_setup_conditions(
        direction=SignalDirection.CALL,
        price=Decimal("100.30"),
        vwap=Decimal(100),
        close=Decimal("100.30"),
        market_structure=MarketStructureState.BULLISH,
        level=Decimal(100),
        rvol=Decimal(2),
        atr14=Decimal(2),
        overall_market_regime=OverallMarketRegime.SUPPORTIVE,
        event_severity=EventSeverity.HIGH,
    )
    assert conditions.event_risk_acceptable is False
    assert conditions.is_full_candidate is False


def test_setup_conditions_pre_breakout_ok_true_even_without_breakout_yet():
    # This is what lets Timing Engine report READY instead of WAIT.
    conditions = evaluate_setup_conditions(
        direction=SignalDirection.CALL,
        price=Decimal("100.30"),
        vwap=Decimal(100),
        close=Decimal("100.10"),  # not yet past the breakout level
        market_structure=MarketStructureState.BULLISH,
        level=Decimal(100),
        rvol=Decimal(1),  # below threshold, breakout not confirmed
        atr14=Decimal(2),
        overall_market_regime=OverallMarketRegime.SUPPORTIVE,
        event_severity=EventSeverity.LOW,
    )
    assert conditions.pre_breakout_ok is True
    assert conditions.breakout_confirmed is False
