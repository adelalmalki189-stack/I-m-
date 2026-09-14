"""Previous-day and running intraday high/low.

Not a separate formula in docs/CALCULATION_SPEC_V0.1.md, but required to
populate technical_features.pdh/pdl/day_high/day_low (docs/DATA_SPEC_V0.1.md §4).
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.data_providers.schemas import Candle


def previous_day_high_low(previous_session_candles: Sequence[Candle]) -> tuple[Decimal, Decimal]:
    """PDH/PDL — a single fixed pair for the whole current session, taken
    from the immediately preceding trading session's candles."""
    if not previous_session_candles:
        raise ValueError("previous_session_candles must not be empty")
    highs = [c.high for c in previous_session_candles]
    lows = [c.low for c in previous_session_candles]
    return max(highs), min(lows)


def running_day_high_low(current_session_candles: Sequence[Candle]) -> list[tuple[Decimal, Decimal]]:
    """Running (day_high, day_low) as of each candle — index i reflects
    only candles[0..i], never a later high/low leaking backward in time."""
    result: list[tuple[Decimal, Decimal]] = []
    running_high: Decimal | None = None
    running_low: Decimal | None = None

    for candle in current_session_candles:
        running_high = candle.high if running_high is None else max(running_high, candle.high)
        running_low = candle.low if running_low is None else min(running_low, candle.low)
        result.append((running_high, running_low))

    return result
