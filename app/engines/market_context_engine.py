"""Methodology §1 — Market Regime for SPY/QQQ and VIX-as-risk-context.

VIX thresholds are explicitly V0.1 initial parameters (the original spec's
own words: "لا نضع حدوداً ثابتة نهائية لـVIX قبل جمع البيانات") — tunable
once real outcome data exists, not claimed as optimal.
"""
from __future__ import annotations

from decimal import Decimal

from app.models.enums import MarketRegime, MarketStructureState, OverallMarketRegime, TrendSlope, VixRegime

# V0.1 initial parameters (docs/CALCULATION_SPEC_V0.1.md philosophy applies
# here too: tunable, not claimed optimal).
VIX_NORMAL_MAX = Decimal(20)
VIX_ELEVATED_MAX = Decimal(25)
VIX_HIGH_MAX = Decimal(35)


def classify_market_regime(
    *,
    price: Decimal,
    vwap: Decimal,
    ema20: Decimal | None,
    ema50: Decimal | None,
    ema20_slope: TrendSlope | None,
    market_structure: MarketStructureState | None,
) -> MarketRegime | None:
    """Methodology §1. Returns None (not NEUTRAL) when EMA/slope are still
    in Feature Engine warm-up — a regime call without a real trend
    measurement is a Data Quality problem, not a valid Neutral reading."""
    if ema20 is None or ema50 is None or ema20_slope is None:
        return None

    bullish = (
        price > vwap
        and ema20 > ema50
        and ema20_slope == TrendSlope.POSITIVE
        and market_structure != MarketStructureState.BEARISH
    )
    bearish = (
        price < vwap
        and ema20 < ema50
        and ema20_slope == TrendSlope.NEGATIVE
        and market_structure != MarketStructureState.BULLISH
    )

    if bullish:
        return MarketRegime.BULLISH
    if bearish:
        return MarketRegime.BEARISH
    return MarketRegime.NEUTRAL


def classify_vix_regime(vix_value: Decimal) -> VixRegime:
    if vix_value < VIX_NORMAL_MAX:
        return VixRegime.NORMAL
    if vix_value < VIX_ELEVATED_MAX:
        return VixRegime.ELEVATED
    if vix_value < VIX_HIGH_MAX:
        return VixRegime.HIGH
    return VixRegime.EXTREME


def combine_overall_regime(
    *,
    spy_regime: MarketRegime | None,
    qqq_regime: MarketRegime | None,
    vix_regime: VixRegime,
) -> OverallMarketRegime:
    """V0.1 initial combination rule (not specified verbatim in the
    original methodology beyond one worked example): VIX HIGH/EXTREME
    always caps the result at RESTRICTIVE regardless of SPY/QQQ, since
    Methodology §1 treats VIX purely as a risk brake, never a booster."""
    if vix_regime in (VixRegime.HIGH, VixRegime.EXTREME):
        return OverallMarketRegime.RESTRICTIVE

    regimes = [r for r in (spy_regime, qqq_regime) if r is not None]
    if len(regimes) < 2:
        return OverallMarketRegime.NEUTRAL

    if all(r == MarketRegime.BULLISH for r in regimes):
        return OverallMarketRegime.SUPPORTIVE
    if all(r == MarketRegime.BEARISH for r in regimes):
        return OverallMarketRegime.RESTRICTIVE
    return OverallMarketRegime.NEUTRAL
