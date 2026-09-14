"""docs/DATA_SPEC_V0.1.md §3 — candles_5m.

`is_complete` is the field that enforces the golden rule in
docs/CALCULATION_SPEC_V0.1.md: no indicator may be computed on a candle
that hasn't closed yet. A row is only written once is_complete=True.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Candle5m(Base):
    __tablename__ = "candles_5m"

    symbol: Mapped[str] = mapped_column(
        String(10), ForeignKey("instruments.symbol"), primary_key=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)

    open: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vwap: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
