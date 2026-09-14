"""docs/CALCULATION_SPEC_V0.1.md §4 — True Range and ATR14.

    TR_i    = max(high_i - low_i, |high_i - close_{i-1}|, |low_i - close_{i-1}|)
    ATR14_i = EMA(TR, period=14)

ATR is the tool this repo added on top of the original spec (which never
defined a volatility measure) to turn Entry Zone, Invalidation, and the
minimum breakout distance from arbitrary numbers into something that
scales with each symbol's own volatility.
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.data_providers.schemas import Candle
from app.engines.feature_engine.ema import compute_ema

DEFAULT_ATR_PERIOD = 14


def compute_true_range(candles: Sequence[Candle]) -> list[Decimal]:
    """First candle's TR is just high-low (no prior close to compare against)."""
    result: list[Decimal] = []
    for i, candle in enumerate(candles):
        high_low = candle.high - candle.low
        if i == 0:
            result.append(high_low)
            continue
        prev_close = candles[i - 1].close
        result.append(
            max(high_low, abs(candle.high - prev_close), abs(candle.low - prev_close))
        )
    return result


def compute_atr(candles: Sequence[Candle], period: int = DEFAULT_ATR_PERIOD) -> list[Decimal | None]:
    true_ranges = compute_true_range(candles)
    return compute_ema(true_ranges, period)
