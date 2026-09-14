"""Test-only Candle factory — not a DataProvider, so it's exempt from the
no-mock-data boundary rule (it never touches app/, only tests/)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.data_providers.schemas import Candle
from app.models.enums import DataStatus

SESSION_OPEN = datetime(2026, 1, 5, 9, 30, tzinfo=UTC)  # an arbitrary Monday


def make_candle(
    *,
    symbol: str = "TEST",
    index: int = 0,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int = 1000,
    session_open: datetime = SESSION_OPEN,
    is_complete: bool = True,
) -> Candle:
    return Candle(
        symbol=symbol,
        timestamp=session_open + timedelta(minutes=5 * index),
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=volume,
        vwap=Decimal(str(close)),
        is_complete=is_complete,
        source="TEST",
        data_status=DataStatus.VALID,
    )


def make_flat_session(
    *,
    symbol: str = "TEST",
    n: int = 10,
    price: float = 100.0,
    volume: int = 1000,
    session_open: datetime = SESSION_OPEN,
) -> list[Candle]:
    return [
        make_candle(
            symbol=symbol,
            index=i,
            open_=price,
            high=price,
            low=price,
            close=price,
            volume=volume,
            session_open=session_open,
        )
        for i in range(n)
    ]
