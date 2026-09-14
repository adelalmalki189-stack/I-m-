"""docs/DATA_SPEC_V0.1.md §6 — options_contracts (dimension table).

option_quotes is intentionally NOT a standalone historical table: per the
Data Spec, live option quotes live in Redis and are only persisted to
Postgres as part of a signal_snapshots row at decision time.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import OptionType


class OptionContract(Base):
    __tablename__ = "options_contracts"

    contract_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(10), ForeignKey("instruments.symbol"), nullable=False)
    option_type: Mapped[OptionType] = mapped_column(Enum(OptionType, native_enum=False), nullable=False)
    strike: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    expiration: Mapped[date] = mapped_column(Date, nullable=False)
    dte: Mapped[int] = mapped_column(Integer, nullable=False)
    stale_after_split: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
