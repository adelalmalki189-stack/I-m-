"""docs/DATA_SPEC_V0.1.md §16 — outcomes (MFE/MAE tracking, Methodology §14)."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Outcome(Base):
    __tablename__ = "outcomes"

    signal_id: Mapped[str] = mapped_column(String(36), ForeignKey("signals.signal_id"), primary_key=True)
    # 5, 10, 15, 30, 60 — one row per checkpoint per Methodology §14
    offset_minutes: Mapped[int] = mapped_column(Integer, primary_key=True)

    underlying_price_at_offset: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    option_price_at_offset: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))

    mfe: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    mae: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
