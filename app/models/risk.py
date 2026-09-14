"""docs/DATA_SPEC_V0.1.md §13 (risk_plans) and
docs/OPERATIONAL_SPEC_V0.1.md §5 (portfolio_risk_config)."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid


class RiskPlan(Base):
    __tablename__ = "risk_plans"

    signal_id: Mapped[str] = mapped_column(String(36), ForeignKey("signals.signal_id"), primary_key=True)

    account_size: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    risk_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    max_risk: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    entry_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    stop_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    target_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)

    risk_per_contract: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # docs/DATA_SPEC_V0.1.md §13: floor(max_risk / risk_per_contract), 0 -> NO TRADE
    # (never rounded up to 1 — see docs/TESTING_SPEC_V0.1.md §3 Risk Engine tests).
    recommended_quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    max_loss: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    potential_profit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    r_multiple: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)


class PortfolioRiskConfig(Base):
    __tablename__ = "portfolio_risk_config"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)

    # Methodology §16-17's per-account settings ("إعداد المستخدم") — kept on
    # this same row rather than a separate single-purpose table, since
    # docs/API_SPEC_V0.1.md §4 groups both under one GET/PUT /risk/config.
    account_size: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal(2000))
    risk_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal(1))

    max_concurrent_signals: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    max_concurrent_per_sector: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    daily_max_loss_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal(3))
    max_total_deployed_risk_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal(5)
    )
    enforce_pdt_limit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
