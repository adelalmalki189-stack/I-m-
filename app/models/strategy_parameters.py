"""docs/DATA_SPEC_V0.1.md §10 — strategy_parameters.

Every derived value elsewhere in the schema carries a `calc_version`
pointing back to a row here, so a later audit can always answer "which
version of the formulas produced this number?" (docs/TESTING_SPEC_V0.1.md §5).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StrategyParameters(Base):
    __tablename__ = "strategy_parameters"

    version: Mapped[str] = mapped_column(String(10), primary_key=True)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
