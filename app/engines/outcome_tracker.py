"""Outcome Tracker — Methodology §14 (MFE/MAE at +5/10/15/30/60 minutes).

Pure calculation only, same architecture as every other engine: the
DB-aware orchestration (which candles to fetch, which checkpoints have
already elapsed, upserting Outcome rows) lives in
app/scripts/run_outcome_tracker.py, so this function is trivially testable
with a plain list of candles.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.data_providers.schemas import Candle
from app.models.enums import SignalDirection

OUTCOME_OFFSETS_MINUTES = (5, 10, 15, 30, 60)


@dataclass(frozen=True, slots=True)
class OutcomeAtOffset:
    price_at_offset: Decimal
    mfe: Decimal
    mae: Decimal


def compute_outcome_at_offset(
    *,
    direction: SignalDirection,
    entry_price: Decimal,
    candles_since_entry: Sequence[Candle],
) -> OutcomeAtOffset:
    """`candles_since_entry` must be every closed candle from entry up to
    and including the checkpoint being measured, in chronological order.

    MFE/MAE are magnitudes (never negative) per Methodology §14's own
    wording ("كم كان أقصى تحرك لصالح/ضد الصفقة") — direction is already
    baked into which extreme counts as favorable vs adverse.
    """
    if not candles_since_entry:
        raise ValueError("candles_since_entry must not be empty")

    price_at_offset = candles_since_entry[-1].close
    highest = max(c.high for c in candles_since_entry)
    lowest = min(c.low for c in candles_since_entry)

    if direction == SignalDirection.CALL:
        mfe = highest - entry_price
        mae = entry_price - lowest
    else:
        mfe = entry_price - lowest
        mae = highest - entry_price

    return OutcomeAtOffset(
        price_at_offset=price_at_offset,
        mfe=max(mfe, Decimal(0)),
        mae=max(mae, Decimal(0)),
    )
