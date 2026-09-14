from __future__ import annotations

from decimal import Decimal

import pytest

from app.engines.outcome_tracker import compute_outcome_at_offset
from app.models.enums import SignalDirection
from tests.engines.factories import make_candle


def test_call_mfe_mae_from_high_low_extremes():
    candles = [
        make_candle(index=0, open_=100, high=103, low=99, close=101),
        make_candle(index=1, open_=101, high=106, low=100, close=105),
        make_candle(index=2, open_=105, high=104, low=97, close=98),
    ]
    result = compute_outcome_at_offset(
        direction=SignalDirection.CALL, entry_price=Decimal(100), candles_since_entry=candles
    )
    assert result.price_at_offset == Decimal(98)  # close of the last candle
    assert result.mfe == Decimal(6)  # highest high (106) - entry (100)
    assert result.mae == Decimal(3)  # entry (100) - lowest low (97)


def test_put_mirrors_call_direction():
    candles = [
        make_candle(index=0, open_=100, high=101, low=94, close=95),
        make_candle(index=1, open_=95, high=103, low=93, close=102),
    ]
    result = compute_outcome_at_offset(
        direction=SignalDirection.PUT, entry_price=Decimal(100), candles_since_entry=candles
    )
    assert result.mfe == Decimal(7)  # entry(100) - lowest low(93)
    assert result.mae == Decimal(3)  # highest high(103) - entry(100)


def test_mfe_mae_never_negative_even_if_price_never_moved_favorably():
    # A CALL where price only ever dropped: MFE should clamp to 0, not go negative.
    candles = [make_candle(index=0, open_=100, high=99, low=90, close=92)]
    result = compute_outcome_at_offset(
        direction=SignalDirection.CALL, entry_price=Decimal(100), candles_since_entry=candles
    )
    assert result.mfe == Decimal(0)
    assert result.mae == Decimal(10)


def test_raises_on_empty_candle_window():
    with pytest.raises(ValueError):
        compute_outcome_at_offset(direction=SignalDirection.CALL, entry_price=Decimal(100), candles_since_entry=[])
