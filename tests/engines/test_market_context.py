from __future__ import annotations

from decimal import Decimal

from app.engines.market_context_engine import (
    classify_market_regime,
    classify_vix_regime,
    combine_overall_regime,
)
from app.models.enums import MarketRegime, MarketStructureState, OverallMarketRegime, TrendSlope, VixRegime


def test_regime_none_during_warmup():
    result = classify_market_regime(
        price=Decimal(100), vwap=Decimal(99), ema20=None, ema50=None, ema20_slope=None,
        market_structure=None,
    )
    assert result is None


def test_regime_bullish_requires_all_conditions():
    result = classify_market_regime(
        price=Decimal(105), vwap=Decimal(100), ema20=Decimal(102), ema50=Decimal(100),
        ema20_slope=TrendSlope.POSITIVE, market_structure=MarketStructureState.BULLISH,
    )
    assert result == MarketRegime.BULLISH


def test_regime_neutral_when_only_some_conditions_hold():
    # Price above VWAP but EMA20 below EMA50 -> not a clean bullish read.
    result = classify_market_regime(
        price=Decimal(105), vwap=Decimal(100), ema20=Decimal(98), ema50=Decimal(100),
        ema20_slope=TrendSlope.POSITIVE, market_structure=MarketStructureState.BULLISH,
    )
    assert result == MarketRegime.NEUTRAL


def test_regime_bearish_requires_all_conditions():
    result = classify_market_regime(
        price=Decimal(95), vwap=Decimal(100), ema20=Decimal(98), ema50=Decimal(100),
        ema20_slope=TrendSlope.NEGATIVE, market_structure=MarketStructureState.BEARISH,
    )
    assert result == MarketRegime.BEARISH


def test_vix_regime_thresholds():
    assert classify_vix_regime(Decimal(15)) == VixRegime.NORMAL
    assert classify_vix_regime(Decimal(22)) == VixRegime.ELEVATED
    assert classify_vix_regime(Decimal(30)) == VixRegime.HIGH
    assert classify_vix_regime(Decimal(40)) == VixRegime.EXTREME


def test_overall_regime_supportive_example_from_spec():
    # SPY Bullish, QQQ Bullish, VIX Normal -> Overall SUPPORTIVE
    # (the exact worked example from the original methodology, §6 Part 2).
    result = combine_overall_regime(
        spy_regime=MarketRegime.BULLISH, qqq_regime=MarketRegime.BULLISH, vix_regime=VixRegime.NORMAL
    )
    assert result == OverallMarketRegime.SUPPORTIVE


def test_overall_regime_restrictive_when_vix_high_even_if_spy_qqq_bullish():
    result = combine_overall_regime(
        spy_regime=MarketRegime.BULLISH, qqq_regime=MarketRegime.BULLISH, vix_regime=VixRegime.HIGH
    )
    assert result == OverallMarketRegime.RESTRICTIVE


def test_overall_regime_neutral_on_mixed_spy_qqq():
    result = combine_overall_regime(
        spy_regime=MarketRegime.BULLISH, qqq_regime=MarketRegime.BEARISH, vix_regime=VixRegime.NORMAL
    )
    assert result == OverallMarketRegime.NEUTRAL
