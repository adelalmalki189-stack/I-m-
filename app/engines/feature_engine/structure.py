"""docs/CALCULATION_SPEC_V0.1.md §5 — Swing High/Low and Market Structure.

Swing confirmation is inherently lagged by `k` candles (a swing at index i
needs k candles on both sides to confirm), so "the current market
structure" as reported here is always "as of k*5 minutes ago" — this is a
known, documented trade-off, not a bug (§5.2).

Breakout detection (§6) is explicitly out of scope for this module — that
belongs to the Setup Engine (Phase 2 of docs/IMPLEMENTATION_PLAN_V0.1.md),
which consumes the swings this module produces.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.data_providers.schemas import Candle
from app.models.enums import MarketStructureState

DEFAULT_SWING_K = 2


@dataclass(frozen=True, slots=True)
class ConfirmedSwing:
    index: int
    confirmed_at_index: int
    price: Decimal
    kind: str  # "HIGH" | "LOW"


def find_confirmed_swings(candles: Sequence[Candle], k: int = DEFAULT_SWING_K) -> list[ConfirmedSwing]:
    """Fractal swings: candle i is a Swing High iff its high exceeds every
    high in [i-k, i+k] excluding itself (Swing Low: symmetric on low)."""
    swings: list[ConfirmedSwing] = []
    n = len(candles)

    for i in range(k, n - k):
        window = range(i - k, i + k + 1)

        if all(candles[i].high > candles[j].high for j in window if j != i):
            swings.append(
                ConfirmedSwing(index=i, confirmed_at_index=i + k, price=candles[i].high, kind="HIGH")
            )
        if all(candles[i].low < candles[j].low for j in window if j != i):
            swings.append(
                ConfirmedSwing(index=i, confirmed_at_index=i + k, price=candles[i].low, kind="LOW")
            )

    return sorted(swings, key=lambda s: s.index)


@dataclass(frozen=True, slots=True)
class SwingSnapshot:
    """The market-structure-relevant state as known at a given candle index."""

    swing_high: Decimal | None
    swing_low: Decimal | None
    swing_confirmed_at_index: int | None  # index of the most recently confirmed swing (either kind)
    market_structure: MarketStructureState | None


def build_swing_snapshots(candles: Sequence[Candle], swings: Sequence[ConfirmedSwing]) -> list[SwingSnapshot]:
    """One snapshot per candle, reflecting only swings confirmed by that index
    (i.e. `confirmed_at_index <= i`) — never a swing from the future."""
    n = len(candles)
    by_confirmation = sorted(swings, key=lambda s: s.confirmed_at_index)

    highs: list[ConfirmedSwing] = []
    lows: list[ConfirmedSwing] = []
    result: list[SwingSnapshot] = []
    pointer = 0
    last_confirmed_index: int | None = None

    for i in range(n):
        while pointer < len(by_confirmation) and by_confirmation[pointer].confirmed_at_index <= i:
            swing = by_confirmation[pointer]
            (highs if swing.kind == "HIGH" else lows).append(swing)
            last_confirmed_index = swing.index
            pointer += 1

        structure: MarketStructureState | None = None
        if len(highs) >= 2 and len(lows) >= 2:
            higher_high = highs[-1].price > highs[-2].price
            higher_low = lows[-1].price > lows[-2].price
            lower_low = lows[-1].price < lows[-2].price
            lower_high = highs[-1].price < highs[-2].price

            if higher_high and higher_low:
                structure = MarketStructureState.BULLISH
            elif lower_low and lower_high:
                structure = MarketStructureState.BEARISH
            else:
                structure = MarketStructureState.NEUTRAL

        result.append(
            SwingSnapshot(
                swing_high=highs[-1].price if highs else None,
                swing_low=lows[-1].price if lows else None,
                swing_confirmed_at_index=last_confirmed_index,
                market_structure=structure,
            )
        )

    return result
