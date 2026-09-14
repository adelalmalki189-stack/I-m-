"""Provider-agnostic domain objects.

Per the original spec (Part 2 §2): "النظام نفسه لا يعرف من أين جاءت
البيانات. يعرف فقط: UnderlyingQuote, OptionQuote, Candle, MarketContext."
No engine anywhere in this codebase may import an IBKR/Barchart-specific
type — everything crosses the DataProvider boundary as one of these.

Each object also carries `source` and `data_status` per the Data Pipeline
in Part 2 §3 (RAW -> VALIDATE -> NORMALIZE -> ...): normalization happens
once, here, at the provider boundary — not scattered across engines.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.models.enums import DataStatus, OptionType


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    vwap: Decimal
    is_complete: bool
    source: str
    data_status: DataStatus


@dataclass(frozen=True, slots=True)
class UnderlyingQuote:
    symbol: str
    timestamp: datetime
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: int
    source: str
    data_status: DataStatus


@dataclass(frozen=True, slots=True)
class OptionContractInfo:
    contract_id: str
    symbol: str
    option_type: OptionType
    strike: Decimal
    expiration: date
    dte: int


@dataclass(frozen=True, slots=True)
class OptionQuote:
    contract_id: str
    timestamp: datetime
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: int
    open_interest: int
    iv: Decimal | None
    delta: Decimal | None
    gamma: Decimal | None
    theta: Decimal | None
    vega: Decimal | None
    source: str
    data_status: DataStatus

    @property
    def spread_pct(self) -> Decimal:
        """docs/CALCULATION_SPEC_V0.1.md / Methodology §12: (ask-bid)/ask*100."""
        if self.ask <= 0:
            return Decimal("100")
        return (self.ask - self.bid) / self.ask * Decimal(100)


@dataclass(frozen=True, slots=True)
class MarketContextData:
    timestamp: datetime
    spy_price: Decimal
    qqq_price: Decimal
    vix_value: Decimal
    source: str
    data_status: DataStatus
