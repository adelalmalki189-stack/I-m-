"""docs/DATA_SPEC_V0.1.md §18 — iv_history.

New entity the original spec never mentioned; required to compute IV Rank
(docs/CALCULATION_SPEC_V0.1.md §9.5) instead of scoring raw IV in isolation.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class IVHistory(Base):
    __tablename__ = "iv_history"

    symbol: Mapped[str] = mapped_column(String(10), ForeignKey("instruments.symbol"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    atm_iv: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
