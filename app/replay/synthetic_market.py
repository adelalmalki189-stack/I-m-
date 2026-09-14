"""Deterministic synthetic multi-day market data for Replay Mode.

No real historical data source is wired into this sandbox — the same
honest limitation already documented on every golden fixture since Phase
1. This generator exists only to exercise the full engine chain across
many days/symbols for the Rule Sanity Check (docs/BACKTEST_PLAN_V0.1.md);
it is not a claim about real market statistics.
"""
from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.data_providers.schemas import Candle
from app.models.enums import DataStatus

BARS_PER_SESSION = 78  # 09:30-16:00 in 5-minute bars
BASE_VOLUME = 50_000
DRIFT_START_FRACTION = 3  # trend drift/volume spike kicks in ~1/3 into the session


def generate_daily_session(
    *,
    symbol: str,
    day_index: int,
    prev_close: Decimal,
    rng: random.Random,
    base_open: datetime,
    trend_day: bool = False,
    direction_bias: int = 0,
) -> list[Candle]:
    """One session of 5m candles via a seeded log-return random walk.
    `trend_day=True` layers a directional drift plus a volume ramp
    starting a third of the way through the session — a synthetic stand-in
    for an intraday breakout, since a pure random walk essentially never
    produces one on its own within 78 bars."""
    session_open = base_open + timedelta(days=day_index)
    candles: list[Candle] = []
    price = prev_close
    drift_start = BARS_PER_SESSION // DRIFT_START_FRACTION

    for i in range(BARS_PER_SESSION):
        noise = Decimal(str(rng.gauss(0, 0.0015)))
        drift = Decimal(0)
        volume = BASE_VOLUME + rng.randint(-5000, 5000)

        if trend_day and i >= drift_start:
            drift = Decimal("0.0012") * direction_bias
            volume = int(BASE_VOLUME * rng.uniform(2.0, 3.5))

        open_ = price
        close = max(open_ * (Decimal(1) + drift + noise), Decimal("0.01"))
        high = max(open_, close) * (Decimal(1) + Decimal(str(abs(rng.gauss(0, 0.0008)))))
        low = min(open_, close) * (Decimal(1) - Decimal(str(abs(rng.gauss(0, 0.0008)))))
        low = max(low, Decimal("0.01"))

        candles.append(
            Candle(
                symbol=symbol,
                timestamp=session_open + timedelta(minutes=5 * i),
                open=open_.quantize(Decimal("0.01")),
                high=high.quantize(Decimal("0.01")),
                low=low.quantize(Decimal("0.01")),
                close=close.quantize(Decimal("0.01")),
                volume=max(volume, 100),
                vwap=close.quantize(Decimal("0.01")),  # placeholder; the pipeline recomputes VWAP itself
                is_complete=True,
                source="SYNTHETIC",
                data_status=DataStatus.VALID,
            )
        )
        price = close

    return candles


def generate_symbol_history(
    *,
    symbol: str,
    num_days: int,
    seed: int,
    start_price: Decimal = Decimal(100),
    trend_day_probability: float = 0.25,
    base_open: datetime = datetime(2026, 1, 5, 9, 30, tzinfo=UTC),
) -> list[list[Candle]]:
    """Deterministic given the same (symbol, num_days, seed) — same seed
    always reproduces the same session series, which is what lets the
    Rule Sanity Check's diagnostics be compared run to run."""
    rng = random.Random(seed)
    sessions: list[list[Candle]] = []
    price = start_price

    for day in range(num_days):
        trend_day = rng.random() < trend_day_probability
        direction_bias = rng.choice([1, -1]) if trend_day else 0
        session = generate_daily_session(
            symbol=symbol, day_index=day, prev_close=price, rng=rng,
            base_open=base_open, trend_day=trend_day, direction_bias=direction_bias,
        )
        sessions.append(session)
        price = session[-1].close

    return sessions
