"""Exercises FakeDataProvider against the DataProvider contract, plus the
outage/reconnection scenarios in docs/OPERATIONAL_SPEC_V0.1.md §3."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.data_providers.schemas import Candle
from app.models.enums import DataStatus
from tests.fakes.fake_data_provider import FakeDataProvider


def _make_candle(symbol: str, ts: datetime, close: str = "100.00") -> Candle:
    return Candle(
        symbol=symbol,
        timestamp=ts,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=1000,
        vwap=Decimal(close),
        is_complete=True,
        source="FAKE",
        data_status=DataStatus.VALID,
    )


async def test_connect_disconnect_lifecycle():
    provider = FakeDataProvider()
    assert provider.is_connected is False

    await provider.connect()
    assert provider.is_connected is True

    await provider.disconnect()
    assert provider.is_connected is False


async def test_simulated_outage_raises_and_recovers():
    provider = FakeDataProvider()
    provider.fail_next_connect = True

    with pytest.raises(ConnectionError):
        await provider.connect()
    assert provider.is_connected is False

    # docs/OPERATIONAL_SPEC_V0.1.md §3 — reconnection should succeed on retry.
    await provider.connect()
    assert provider.is_connected is True


async def test_historical_candles_filters_by_range():
    provider = FakeDataProvider()
    early = _make_candle("SPY", datetime(2026, 1, 2, 9, 30, tzinfo=UTC))
    late = _make_candle("SPY", datetime(2026, 1, 2, 15, 55, tzinfo=UTC))
    provider.historical_candles["SPY"] = [early, late]

    result = await provider.get_historical_candles_5m(
        "SPY",
        datetime(2026, 1, 2, 9, 0, tzinfo=UTC),
        datetime(2026, 1, 2, 10, 0, tzinfo=UTC),
    )
    assert result == [early]


async def test_subscribe_candles_5m_only_fires_on_closed_candles():
    provider = FakeDataProvider()
    received: list[Candle] = []

    async def on_candle(candle: Candle) -> None:
        received.append(candle)

    await provider.subscribe_candles_5m("SPY", on_candle)

    closed = _make_candle("SPY", datetime(2026, 1, 2, 9, 35, tzinfo=UTC))
    await provider.push_candle(closed)

    assert received == [closed]
    assert all(c.is_complete for c in received)
