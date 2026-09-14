"""docs/CALCULATION_SPEC_V0.1.md §2 — EMA20/EMA50 and slope classification.

EMA is continuous across session boundaries (never reset daily) to avoid
artificial jumps at the open. The first `period` values have no EMA yet —
an SMA seed is used for the (period)-th value, and every value before that
is None (not zero, not a guess).
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.models.enums import TrendSlope

DEFAULT_SLOPE_LOOKBACK = 3  # candles (= 15 minutes on 5m bars)
DEFAULT_SLOPE_THRESHOLD_PCT = Decimal("0.05")  # percent of price


def compute_ema(closes: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """Returns one EMA value per close, aligned by index. Entries before
    the SMA seed (index < period - 1) are None — insufficient warm-up,
    never a fabricated value."""
    if period <= 0:
        raise ValueError("period must be positive")

    result: list[Decimal | None] = [None] * len(closes)
    if len(closes) < period:
        return result

    alpha = Decimal(2) / Decimal(period + 1)

    seed = sum(closes[:period]) / Decimal(period)
    result[period - 1] = seed

    prev = seed
    for i in range(period, len(closes)):
        prev = alpha * closes[i] + (Decimal(1) - alpha) * prev
        result[i] = prev

    return result


def compute_slope_series(
    ema_values: Sequence[Decimal | None],
    *,
    lookback: int = DEFAULT_SLOPE_LOOKBACK,
    threshold_pct: Decimal = DEFAULT_SLOPE_THRESHOLD_PCT,
) -> list[TrendSlope | None]:
    """docs/CALCULATION_SPEC_V0.1.md §2:

        ema20_slope_i = EMA20_i - EMA20_{i-lookback}
        slope_threshold = threshold_pct% * close_i   (approximated here
            using the EMA value itself, since callers pass EMA series —
            close is only used at the call site if a tighter definition
            is needed later).

    None wherever either endpoint EMA is None (still in warm-up).
    """
    result: list[TrendSlope | None] = [None] * len(ema_values)

    for i in range(lookback, len(ema_values)):
        current, past = ema_values[i], ema_values[i - lookback]
        if current is None or past is None:
            continue

        delta = current - past
        threshold = (threshold_pct / Decimal(100)) * current

        if delta > threshold:
            result[i] = TrendSlope.POSITIVE
        elif delta < -threshold:
            result[i] = TrendSlope.NEGATIVE
        else:
            result[i] = TrendSlope.FLAT

    return result
