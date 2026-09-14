"""Shared enum types used across ORM models.

Every enum here traces back to a specific value list in docs/DATA_SPEC_V0.1.md
or the original methodology spec — no enum member is invented ad hoc.
"""
from __future__ import annotations

import enum


class DataStatus(enum.StrEnum):
    """docs/OPERATIONAL_SPEC_V0.1.md §2 — staleness/validity classification."""

    VALID = "VALID"
    STALE = "STALE"
    INVALID = "INVALID"


class OptionType(enum.StrEnum):
    CALL = "CALL"
    PUT = "PUT"


class SignalDirection(enum.StrEnum):
    CALL = "CALL"
    PUT = "PUT"


class SignalStatus(enum.StrEnum):
    """Methodology §8 — the Timing Engine's four states, plus EXPIRED
    (docs/DATA_SPEC_V0.1.md §11 signals.status)."""

    WAIT = "WAIT"
    READY = "READY"
    ENTRY = "ENTRY"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class MarketStructureState(enum.StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class TrendSlope(enum.StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    FLAT = "FLAT"


class MarketRegime(enum.StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class VixRegime(enum.StrEnum):
    """Methodology §1 — VIX as risk context, not a standalone entry signal."""

    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


class OverallMarketRegime(enum.StrEnum):
    SUPPORTIVE = "SUPPORTIVE"
    NEUTRAL = "NEUTRAL"
    RESTRICTIVE = "RESTRICTIVE"


class EventType(enum.StrEnum):
    EARNINGS = "EARNINGS"
    CPI = "CPI"
    FOMC = "FOMC"
    NFP = "NFP"
    FED_SPEECH = "FED_SPEECH"
    ECONOMIC_DATA = "ECONOMIC_DATA"


class EventSeverity(enum.StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


class SessionType(enum.StrEnum):
    """docs/OPERATIONAL_SPEC_V0.1.md §1 — trading_calendar.session_type."""

    FULL = "FULL"
    HALF = "HALF"
    CLOSED = "CLOSED"


class HardGateStatus(enum.StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class ExitReason(enum.StrEnum):
    """Methodology §20 — Exit Rules."""

    STOP_HIT = "STOP_HIT"
    TARGET_HIT = "TARGET_HIT"
    UNDERLYING_INVALIDATION = "UNDERLYING_INVALIDATION"
    VWAP_FAILURE = "VWAP_FAILURE"
    MOMENTUM_FAILURE = "MOMENTUM_FAILURE"
    BREAKOUT_FAILURE = "BREAKOUT_FAILURE"
    TIME_STOP = "TIME_STOP"
    EVENT_RISK_ESCALATION = "EVENT_RISK_ESCALATION"
    END_OF_SESSION = "END_OF_SESSION"


class NotificationChannel(enum.StrEnum):
    """docs/OPERATIONAL_SPEC_V0.1.md §6 — V0.1 supports PUSH + SOUND."""

    PUSH = "PUSH"
    SOUND = "SOUND"
    WEBHOOK = "WEBHOOK"
    SMS = "SMS"


class DeliveryStatus(enum.StrEnum):
    SENT = "SENT"
    FAILED = "FAILED"


class DataSourceKind(enum.StrEnum):
    """docs/DATA_SPEC_V0.1.md §20 — RAW / DERIVED / CONFIG traceability."""

    RAW = "RAW"
    DERIVED = "DERIVED"
    CONFIG = "CONFIG"
