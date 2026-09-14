"""FakeDataProvider — deterministic in-memory DataProvider for tests only.

docs/TESTING_SPEC_V0.1.md §2: this class must never be importable from
production code. It deliberately lives under tests/ (outside the `app`
package) rather than app/data_providers/, and
tests/test_no_mock_data_boundary.py asserts nothing under app/ imports it.

It also lets tests simulate the failure modes in
docs/OPERATIONAL_SPEC_V0.1.md §3 (disconnect, stale/invalid data) without a
real broker connection.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from app.data_providers.base import DataProvider
from app.data_providers.schemas import (
    Candle,
    MarketContextData,
    OptionContractInfo,
    OptionQuote,
    UnderlyingQuote,
)


class FakeDataProvider(DataProvider):
    def __init__(self) -> None:
        self._connected = False
        self.historical_candles: dict[str, list[Candle]] = {}
        self.underlying_quotes: dict[str, UnderlyingQuote] = {}
        self.option_chains: dict[str, list[OptionContractInfo]] = {}
        self.option_quotes: dict[str, OptionQuote] = {}
        self.market_context: MarketContextData | None = None

        self._candle_subscribers: dict[str, list[Callable[[Candle], Awaitable[None]]]] = {}
        self._option_quote_subscribers: dict[str, list[Callable[[OptionQuote], Awaitable[None]]]] = {}

        # Toggle to make connect() raise, simulating an outage — used to
        # exercise the reconnection/circuit-breaker paths in
        # docs/OPERATIONAL_SPEC_V0.1.md §3.
        self.fail_next_connect = False

    async def connect(self) -> None:
        if self.fail_next_connect:
            self.fail_next_connect = False
            raise ConnectionError("simulated IBKR outage")
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def get_historical_candles_5m(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[Candle]:
        candles = self.historical_candles.get(symbol, [])
        return [c for c in candles if start <= c.timestamp <= end]

    async def subscribe_candles_5m(
        self, symbol: str, callback: Callable[[Candle], Awaitable[None]]
    ) -> None:
        self._candle_subscribers.setdefault(symbol, []).append(callback)

    async def push_candle(self, candle: Candle) -> None:
        """Test helper: simulate a newly-closed 5m candle arriving live."""
        self.historical_candles.setdefault(candle.symbol, []).append(candle)
        for cb in self._candle_subscribers.get(candle.symbol, []):
            await cb(candle)

    async def get_underlying_quote(self, symbol: str) -> UnderlyingQuote:
        return self.underlying_quotes[symbol]

    async def get_option_chain(self, symbol: str) -> list[OptionContractInfo]:
        return self.option_chains.get(symbol, [])

    async def get_option_quote(self, contract_id: str) -> OptionQuote:
        return self.option_quotes[contract_id]

    async def subscribe_option_quote(
        self, contract_id: str, callback: Callable[[OptionQuote], Awaitable[None]]
    ) -> None:
        self._option_quote_subscribers.setdefault(contract_id, []).append(callback)

    async def push_option_quote(self, quote: OptionQuote) -> None:
        self.option_quotes[quote.contract_id] = quote
        for cb in self._option_quote_subscribers.get(quote.contract_id, []):
            await cb(quote)

    async def get_market_context(self) -> MarketContextData:
        if self.market_context is None:
            raise RuntimeError("FakeDataProvider.market_context not seeded")
        return self.market_context
