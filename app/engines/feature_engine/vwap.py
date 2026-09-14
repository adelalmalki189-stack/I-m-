"""docs/CALCULATION_SPEC_V0.1.md §1 — session-anchored VWAP.

    typical_price_i = (high_i + low_i + close_i) / 3
    cum_pv_i  = cum_pv_{i-1}  + typical_price_i * volume_i
    cum_vol_i = cum_vol_{i-1} + volume_i
    VWAP_i    = cum_pv_i / cum_vol_i

VWAP resets every session — the caller must pass only the candles of a
single trading session, in chronological order, never candles spanning
multiple days.
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.data_providers.schemas import Candle


def compute_session_vwap(session_candles: Sequence[Candle]) -> list[Decimal]:
    """Returns one VWAP value per candle, aligned by index to the input.

    Raises ValueError if any candle has zero cumulative volume up to that
    point (i.e. the very first candle of the session has volume 0) — VWAP
    is undefined there and callers must not silently divide by zero.
    """
    result: list[Decimal] = []
    cum_pv = Decimal(0)
    cum_vol = 0

    for candle in session_candles:
        typical_price = (candle.high + candle.low + candle.close) / Decimal(3)
        cum_pv += typical_price * candle.volume
        cum_vol += candle.volume

        if cum_vol == 0:
            raise ValueError(
                f"Cannot compute VWAP for {candle.symbol} at {candle.timestamp}: "
                "zero cumulative volume"
            )

        result.append(cum_pv / cum_vol)

    return result
