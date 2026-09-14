from __future__ import annotations

from decimal import Decimal

from app.engines.feature_engine.ema import compute_ema, compute_slope_series
from app.models.enums import TrendSlope


def test_ema_none_before_seed():
    closes = [Decimal(100), Decimal(101), Decimal(102)]
    result = compute_ema(closes, period=5)
    assert result == [None, None, None]


def test_ema_seed_is_sma_of_first_period():
    closes = [Decimal(10), Decimal(20), Decimal(30)]
    result = compute_ema(closes, period=3)
    assert result[2] == Decimal(20)  # SMA(10,20,30) = 20
    assert result[0] is None and result[1] is None


def test_ema_continues_incrementally_after_seed():
    closes = [Decimal(10), Decimal(20), Decimal(30), Decimal(40)]
    result = compute_ema(closes, period=3)
    alpha = Decimal(2) / Decimal(4)
    expected_4 = alpha * Decimal(40) + (1 - alpha) * Decimal(20)
    assert result[3] == expected_4


def test_slope_classified_positive_negative_flat():
    # Strongly rising EMA -> POSITIVE
    rising = [Decimal(100), Decimal(100), Decimal(100), Decimal(110)]
    result = compute_slope_series(rising, lookback=3, threshold_pct=Decimal("0.05"))
    assert result[3] == TrendSlope.POSITIVE

    falling = [Decimal(100), Decimal(100), Decimal(100), Decimal(90)]
    result = compute_slope_series(falling, lookback=3, threshold_pct=Decimal("0.05"))
    assert result[3] == TrendSlope.NEGATIVE

    flat = [Decimal(100), Decimal(100), Decimal(100), Decimal("100.001")]
    result = compute_slope_series(flat, lookback=3, threshold_pct=Decimal("0.05"))
    assert result[3] == TrendSlope.FLAT


def test_slope_none_during_warmup_or_missing_ema():
    values: list[Decimal | None] = [None, None, Decimal(100), Decimal(101)]
    result = compute_slope_series(values, lookback=3)
    # index 3 - 3 = 0, which is None -> slope must be None, not fabricated
    assert result[3] is None
