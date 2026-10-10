"""SQLAlchemy models for every persistent table in docs/DATA_SPEC_V0.1.md.

Importing this package registers all models on `Base.metadata`, which is
what Alembic autogenerate and the test-suite's `create_all` rely on.
"""
from app.insurance.models import (
    Customer,
    EmailAuditLog,
    Insurer,
    InsurancePolicy,
    InsuranceQuote,
    InsuranceRequest,
    InsuranceSubmission,
    InsurerTemplate,
    RequestDocument,
    SalesEmployee,
    UnderwritingAssignment,
)
from app.models.base import Base
from app.models.candles import Candle5m
from app.models.instruments import Instrument
from app.models.iv_history import IVHistory
from app.models.market_events import MarketEvent
from app.models.notifications import NotificationLog
from app.models.options import OptionContract
from app.models.outcomes import Outcome
from app.models.risk import PortfolioRiskConfig, RiskPlan
from app.models.signal_snapshots import SignalSnapshot
from app.models.signals import Signal
from app.models.strategy_parameters import StrategyParameters
from app.models.technical_features import TechnicalFeatures
from app.models.trades import Trade
from app.models.trading_calendar import TradingCalendarDay

__all__ = [
    "Base",
    "Instrument",
    "Candle5m",
    "TechnicalFeatures",
    "OptionContract",
    "MarketEvent",
    "TradingCalendarDay",
    "StrategyParameters",
    "Signal",
    "SignalSnapshot",
    "RiskPlan",
    "PortfolioRiskConfig",
    "Trade",
    "Outcome",
    "NotificationLog",
    "IVHistory",
    "Customer",
    "InsuranceRequest",
    "RequestDocument",
    "SalesEmployee",
    "UnderwritingAssignment",
    "Insurer",
    "InsurerTemplate",
    "InsuranceSubmission",
    "InsuranceQuote",
    "InsurancePolicy",
    "EmailAuditLog",
]
