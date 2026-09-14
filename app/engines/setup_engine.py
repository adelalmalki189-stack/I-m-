"""Setup Engine — Methodology §6 (CALL/PUT Setup checklist) plus
docs/CALCULATION_SPEC_V0.1.md §6 (Breakout), §7 (Entry Zone), §8.1
(Invalidation) and §8.2 (Target).

Deliberately stops at the underlying level (docs/IMPLEMENTATION_PLAN_V0.1.md
Phase 2): checklist items 8 ("Suitable option exists") and 9 ("R:R passes
minimum") are Phase 3 (Contract Engine) and Phase 4 (Risk Engine)
concerns — they are represented here only as `None` ("not yet evaluated"),
never silently skipped or assumed true.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.models.enums import EventSeverity, MarketStructureState, OverallMarketRegime, SignalDirection

DEFAULT_RVOL_THRESHOLD = Decimal("1.30")
DEFAULT_MIN_BREAKOUT_ATR_MULT = Decimal("0.10")
DEFAULT_MAX_LEVEL_DISTANCE_ATR_MULT = Decimal("1.5")
DEFAULT_ENTRY_ZONE_IDEAL_ATR_MULT = Decimal("0.5")
DEFAULT_ENTRY_ZONE_MAX_ATR_MULT = Decimal("1.0")
DEFAULT_INVALIDATION_BUFFER_ATR_MULT = Decimal("0.25")
DEFAULT_TARGET_R_MULTIPLE = Decimal(2)
DEFAULT_MAX_ACCEPTABLE_EVENT_SEVERITY = EventSeverity.MEDIUM

_SEVERITY_ORDER = (EventSeverity.LOW, EventSeverity.MEDIUM, EventSeverity.HIGH, EventSeverity.EXTREME)


def select_level(
    *,
    price: Decimal,
    swing_extreme: Decimal | None,
    prior_day_extreme: Decimal | None,
    opening_range_extreme: Decimal | None,
    atr14: Decimal | None,
    max_distance_atr_mult: Decimal = DEFAULT_MAX_LEVEL_DISTANCE_ATR_MULT,
) -> Decimal | None:
    """docs/CALCULATION_SPEC_V0.1.md §6.1 — one function serves both
    resistance (pass swing high / PDH / opening-range high) and support
    (pass swing low / PDL / opening-range low): the nearest candidate
    within `max_distance_atr_mult` * ATR14 of price, falling back to the
    swing level (or nearest candidate) if none are within range."""
    candidates = [c for c in (swing_extreme, prior_day_extreme, opening_range_extreme) if c is not None]
    if not candidates:
        return None

    if atr14 is not None:
        within_range = [c for c in candidates if abs(c - price) <= max_distance_atr_mult * atr14]
        if within_range:
            return min(within_range, key=lambda c: abs(c - price))

    return swing_extreme if swing_extreme is not None else min(candidates, key=lambda c: abs(c - price))


def is_breakout_confirmed(
    *,
    close: Decimal,
    level: Decimal | None,
    rvol: Decimal | None,
    atr14: Decimal | None,
    direction: SignalDirection,
    rvol_threshold: Decimal = DEFAULT_RVOL_THRESHOLD,
    min_breakout_atr_mult: Decimal = DEFAULT_MIN_BREAKOUT_ATR_MULT,
) -> bool:
    """docs/CALCULATION_SPEC_V0.1.md §6.2-6.3. Missing inputs (warm-up,
    INVALID data) never default to "confirmed" — they default to False."""
    if level is None or atr14 is None or rvol is None:
        return False

    distance = (close - level) if direction == SignalDirection.CALL else (level - close)
    if distance <= 0:
        return False
    if rvol < rvol_threshold:
        return False
    return distance >= min_breakout_atr_mult * atr14


@dataclass(frozen=True, slots=True)
class EntryZone:
    ideal_low: Decimal
    ideal_high: Decimal
    max_extension: Decimal
    acceptable_min: Decimal
    acceptable_max: Decimal


def compute_entry_zone(
    *,
    level: Decimal,
    atr14: Decimal,
    direction: SignalDirection,
    ideal_atr_mult: Decimal = DEFAULT_ENTRY_ZONE_IDEAL_ATR_MULT,
    max_atr_mult: Decimal = DEFAULT_ENTRY_ZONE_MAX_ATR_MULT,
) -> EntryZone:
    """docs/CALCULATION_SPEC_V0.1.md §7 — Avoid Chasing. For PUT the zone
    mirrors below the support level; `acceptable_min/max` normalize the
    direction away so callers never need an if/else on direction again."""
    ideal_span = ideal_atr_mult * atr14
    max_span = max_atr_mult * atr14

    if direction == SignalDirection.CALL:
        return EntryZone(
            ideal_low=level,
            ideal_high=level + ideal_span,
            max_extension=level + max_span,
            acceptable_min=level,
            acceptable_max=level + max_span,
        )
    return EntryZone(
        ideal_low=level - ideal_span,
        ideal_high=level,
        max_extension=level - max_span,
        acceptable_min=level - max_span,
        acceptable_max=level,
    )


def compute_invalidation(
    *,
    level: Decimal,
    atr14: Decimal,
    swing_extreme: Decimal | None,
    direction: SignalDirection,
    buffer_atr_mult: Decimal = DEFAULT_INVALIDATION_BUFFER_ATR_MULT,
) -> Decimal:
    """docs/CALCULATION_SPEC_V0.1.md §8.1 — ties the stop to price structure
    instead of an arbitrary number: for CALL, the lower (more conservative)
    of a fixed ATR buffer below the breakout level and the most recent
    confirmed swing low (min(...) per spec — PUT mirrors with max(...))."""
    buffer_ = buffer_atr_mult * atr14
    if direction == SignalDirection.CALL:
        candidate = level - buffer_
        return min(candidate, swing_extreme) if swing_extreme is not None else candidate
    candidate = level + buffer_
    return max(candidate, swing_extreme) if swing_extreme is not None else candidate


def compute_target(
    *,
    entry_price: Decimal,
    invalidation: Decimal,
    direction: SignalDirection,
    r_multiple: Decimal = DEFAULT_TARGET_R_MULTIPLE,
) -> Decimal:
    """docs/CALCULATION_SPEC_V0.1.md §8.2-8.3 (underlying leg only — the
    premium-side translation via delta is Phase 4's Risk Engine)."""
    risk = (entry_price - invalidation) if direction == SignalDirection.CALL else (invalidation - entry_price)
    if risk <= 0:
        raise ValueError("invalidation must be on the correct side of entry_price for a positive risk")

    if direction == SignalDirection.CALL:
        return entry_price + r_multiple * risk
    return entry_price - r_multiple * risk


@dataclass(frozen=True, slots=True)
class SetupConditions:
    """Methodology §6 CALL/PUT checklist, condition-by-condition — this is
    what a future "WHY THIS TRADE?" view (docs/API_SPEC_V0.1.md §3) reads
    from, so each item stays individually inspectable rather than folded
    into a single bool."""

    vwap_aligned: bool  # item 1
    structure_aligned: bool  # item 2
    breakout_confirmed: bool  # items 3-4
    rvol_confirmed: bool  # item 5
    market_context_supportive: bool  # item 6
    event_risk_acceptable: bool  # item 7
    # items 8-9 (suitable option exists / R:R passes) are None here —
    # Phase 3 (Contract Engine) and Phase 4 (Risk Engine) fill them in.

    @property
    def pre_breakout_ok(self) -> bool:
        """Everything except the breakout trigger itself — this is what
        distinguishes Timing Engine's READY (setup forming) from WAIT
        (conditions not even aligned yet)."""
        return (
            self.vwap_aligned
            and self.structure_aligned
            and self.market_context_supportive
            and self.event_risk_acceptable
        )

    @property
    def is_full_candidate(self) -> bool:
        return self.pre_breakout_ok and self.breakout_confirmed and self.rvol_confirmed


def evaluate_setup_conditions(
    *,
    direction: SignalDirection,
    price: Decimal,
    vwap: Decimal,
    close: Decimal,
    market_structure: MarketStructureState | None,
    level: Decimal | None,
    rvol: Decimal | None,
    atr14: Decimal | None,
    overall_market_regime: OverallMarketRegime | None,
    event_severity: EventSeverity | None,
    rvol_threshold: Decimal = DEFAULT_RVOL_THRESHOLD,
    min_breakout_atr_mult: Decimal = DEFAULT_MIN_BREAKOUT_ATR_MULT,
    max_acceptable_event_severity: EventSeverity = DEFAULT_MAX_ACCEPTABLE_EVENT_SEVERITY,
) -> SetupConditions:
    if direction == SignalDirection.CALL:
        vwap_aligned = price > vwap
        structure_aligned = market_structure == MarketStructureState.BULLISH
    else:
        vwap_aligned = price < vwap
        structure_aligned = market_structure == MarketStructureState.BEARISH

    breakout_confirmed = is_breakout_confirmed(
        close=close,
        level=level,
        rvol=rvol,
        atr14=atr14,
        direction=direction,
        rvol_threshold=rvol_threshold,
        min_breakout_atr_mult=min_breakout_atr_mult,
    )
    rvol_confirmed = rvol is not None and rvol >= rvol_threshold
    market_context_supportive = overall_market_regime == OverallMarketRegime.SUPPORTIVE

    event_risk_acceptable = event_severity is None or (
        _SEVERITY_ORDER.index(event_severity) <= _SEVERITY_ORDER.index(max_acceptable_event_severity)
    )

    return SetupConditions(
        vwap_aligned=vwap_aligned,
        structure_aligned=structure_aligned,
        breakout_confirmed=breakout_confirmed,
        rvol_confirmed=rvol_confirmed,
        market_context_supportive=market_context_supportive,
        event_risk_acceptable=event_risk_acceptable,
    )


def opposite_direction_extreme(
    swing_high: Decimal | None, swing_low: Decimal | None, direction: SignalDirection
) -> Decimal | None:
    """Helper for compute_invalidation's `swing_extreme` argument: a CALL's
    invalidation anchors to the swing LOW, a PUT's to the swing HIGH."""
    return swing_low if direction == SignalDirection.CALL else swing_high
