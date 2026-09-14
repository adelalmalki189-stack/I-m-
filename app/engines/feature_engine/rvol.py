"""docs/CALCULATION_SPEC_V0.1.md §3 — RVOL without look-ahead.

    baseline_volume(bucket) = mean(volume(bucket, day) for day in valid_days)
    RVOL_i = current_bucket_volume_i / baseline_volume(bucket_i)

`bucket` here is simply "the i-th 5-minute bar since the session opened"
(index position), since every FULL trading day starts its 5m bars at the
same wall-clock time (09:30 ET) — no separate time-of-day lookup is
needed once callers only pass FULL-day baseline sessions.

This function is deliberately calendar-blind: `valid_days` (the exclusion
of half-days, high-severity-event days, and the current day itself) is a
decision docs/OPERATIONAL_SPEC_V0.1.md and docs/CALCULATION_SPEC_V0.1.md
§3.2 assign to the caller (the pipeline in this package, which has access
to trading_calendar and market_events) — this module only ever sees
baseline sessions that have already been filtered to "valid".
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.data_providers.schemas import Candle

DEFAULT_MIN_VALID_DAYS = 20


def compute_rvol_for_session(
    current_session_candles: Sequence[Candle],
    baseline_sessions: Sequence[Sequence[Candle]],
    *,
    min_valid_days: int = DEFAULT_MIN_VALID_DAYS,
) -> list[Decimal | None]:
    """One RVOL value per candle in `current_session_candles`.

    None (never 0 or a fabricated ratio) whenever:
      - fewer than `min_valid_days` baseline sessions have a candle at
        that bucket index (warm-up — docs/CALCULATION_SPEC_V0.1.md §3.3), or
      - the baseline volume for that bucket is zero (avoid division by zero).
    """
    result: list[Decimal | None] = []

    for i, candle in enumerate(current_session_candles):
        bucket_volumes = [
            session[i].volume for session in baseline_sessions if len(session) > i
        ]

        if len(bucket_volumes) < min_valid_days:
            result.append(None)
            continue

        baseline_volume = Decimal(sum(bucket_volumes)) / Decimal(len(bucket_volumes))
        if baseline_volume == 0:
            result.append(None)
            continue

        result.append(Decimal(candle.volume) / baseline_volume)

    return result
