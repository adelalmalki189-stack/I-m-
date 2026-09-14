"""docs/DATA_SPEC_V0.1.md §15 — trades (manual Trade Journal, no auto-execution)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid
from app.models.enums import ExitReason


class Trade(Base):
    __tablename__ = "trades"

    trade_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    signal_id: Mapped[str] = mapped_column(String(36), ForeignKey("signals.signal_id"), nullable=False)

    user_entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    user_entry_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    user_exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_exit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))

    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    max_profit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    max_loss: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))

    exit_reason: Mapped[ExitReason | None] = mapped_column(Enum(ExitReason, native_enum=False))
