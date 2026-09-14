"""DataProvider abstract interface — Part 2 §2 of the original spec.

    DataProvider
        ├── IBKRDataProvider       (production)
        ├── BarchartDataProvider   (future, not built in V0.1)
        └── FakeDataProvider       (tests/fakes/ only — never under app/)

No engine may depend on a concrete provider; every engine takes a
`DataProvider` and only ever sees the dataclasses in schemas.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import datetime

from app.data_providers.schemas import (
    Candle,
    MarketContextData,
    OptionContractInfo,
    OptionQuote,
    UnderlyingQuote,
)

CandleCallback = Callable[[Candle], Awaitable[None]]
OptionQuoteCallback = Callable[[OptionQuote], Awaitable[None]]


class DataProvider(ABC):
    """Every method is async: live providers hit a network broker API, and
    tests must be able to exercise the exact same call shape with a fake."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish the connection. Idempotent — safe to call when already
        connected (needed by the reconnection logic in
        docs/OPERATIONAL_SPEC_V0.1.md §3)."""

    @abstractmethod
    async def disconnect(self) -> None:
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        ...

    @abstractmethod
    async def get_historical_candles_5m(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[Candle]:
        """Only ever returns candles with is_complete=True."""

    @abstractmethod
    async def subscribe_candles_5m(self, symbol: str, callback: CandleCallback) -> None:
        """`callback` fires exactly once per closed 5m candle — never on a
        candle still forming. This is what makes the anti-look-ahead rule
        in docs/CALCULATION_SPEC_V0.1.md enforceable at the provider
        boundary instead of trusted-but-not-verified in every engine."""

    @abstractmethod
    async def get_underlying_quote(self, symbol: str) -> UnderlyingQuote:
        ...

    @abstractmethod
    async def get_option_chain(self, symbol: str) -> list[OptionContractInfo]:
        ...

    @abstractmethod
    async def get_option_quote(self, contract_id: str) -> OptionQuote:
        ...

    @abstractmethod
    async def subscribe_option_quote(self, contract_id: str, callback: OptionQuoteCallback) -> None:
        ...

    @abstractmethod
    async def get_market_context(self) -> MarketContextData:
        """SPY/QQQ/VIX raw prices — regime classification happens in the
        Feature Engine, not here; this method only returns RAW data."""
