"""docs/DATA_SPEC_V0.1.md §12 — signal_snapshots (TradeSnapshot).

Every field here is a COPY frozen at decision time, never a live reference —
that's the entire point: answer "was this a good call given what was known
then?" without hindsight contaminating the record.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SignalSnapshot(Base):
    __tablename__ = "signal_snapshots"

    signal_id: Mapped[str] = mapped_column(String(36), ForeignKey("signals.signal_id"), primary_key=True)

    underlying_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    vwap: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    # Nullable: a signal can legitimately fire before a symbol has 20/50
    # bars of same-day history (EMA warm-up — docs/CALCULATION_SPEC_V0.1.md
    # §2). Faking a value here would violate the "never fabricate" rule
    # applied everywhere else in this schema (e.g. rvol below).
    ema20: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    ema50: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    rvol: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))

    spy_state: Mapped[str | None] = mapped_column(String(20))
    qqq_state: Mapped[str | None] = mapped_column(String(20))
    vix_value: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    sector_state: Mapped[str | None] = mapped_column(String(20))

    contract_id: Mapped[str | None] = mapped_column(String(50))
    strike: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    expiration: Mapped[date | None] = mapped_column(Date)
    dte: Mapped[int | None] = mapped_column()

    bid: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    ask: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    last: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    volume: Mapped[int | None] = mapped_column()
    oi: Mapped[int | None] = mapped_column()
    iv: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    delta: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))

    calc_version: Mapped[str] = mapped_column(
        String(10), ForeignKey("strategy_parameters.version"), nullable=False
    )
