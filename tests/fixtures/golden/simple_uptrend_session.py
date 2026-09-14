"""This repo's first Golden Scenario (docs/TESTING_SPEC_V0.1.md §5).

Not real market data — Phase 1 has no historical-data backfill wired up
yet (that lands with the Options Chain work in later phases), so this is a
deterministic synthetic session instead of a frozen real-market snippet.
It still serves the same purpose: freeze today's Feature Engine output so
a future change to a CALCULATION_SPEC_V0.1.md formula shows up as an
explicit, reviewed diff against `simple_uptrend_session_expected.json`
rather than silent drift.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.data_providers.schemas import Candle
from app.models.enums import DataStatus

SYMBOL = "GOLDNVDA"
SESSION_OPEN = datetime(2026, 2, 2, 9, 30, tzinfo=UTC)  # a Monday
BAR_COUNT = 25
BASELINE_DAY_COUNT = 20


def _candle(
    index: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int,
    *,
    session_open: datetime = SESSION_OPEN,
) -> Candle:
    return Candle(
        symbol=SYMBOL,
        timestamp=session_open + timedelta(minutes=5 * index),
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=volume,
        vwap=Decimal(str(close)),
        is_complete=True,
        source="GOLDEN_FIXTURE",
        data_status=DataStatus.VALID,
    )


def build_current_session() -> list[Candle]:
    """Gentle uptrend, 100 -> 112.5 over 25 bars; volume steps up halfway
    through so RVOL is expected to rise in the second half."""
    candles = []
    price = Decimal("100.00")
    for i in range(BAR_COUNT):
        open_ = price
        close = price + Decimal("0.50")
        high = close + Decimal("0.20")
        low = open_ - Decimal("0.10")
        volume = 1000 if i < 12 else 2500
        candles.append(_candle(i, open_, high, low, close, volume))
        price = close
    return candles


def build_previous_session() -> list[Candle]:
    session_open = SESSION_OPEN - timedelta(days=1)
    return [
        _candle(
            i, 95 + i * 0.1, 95 + i * 0.1 + 0.3, 95 + i * 0.1 - 0.2, 95 + i * 0.1 + 0.1, 900,
            session_open=session_open,
        )
        for i in range(BAR_COUNT)
    ]


def build_baseline_sessions(n: int = BASELINE_DAY_COUNT) -> list[list[Candle]]:
    """Flat 1000-volume baseline at every bucket across all `n` days, so the
    expected RVOL in the second half is exactly 2500/1000 = 2.5 — easy to
    verify by hand rather than trusting the pipeline to check itself."""
    sessions = []
    for d in range(1, n + 1):
        session_open = SESSION_OPEN - timedelta(days=1 + d)
        sessions.append(
            [_candle(i, 100, 100.3, 99.8, 100.1, 1000, session_open=session_open) for i in range(BAR_COUNT)]
        )
    return sessions
