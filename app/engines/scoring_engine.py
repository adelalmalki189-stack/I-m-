"""Scoring Engine — Methodology §22 (Final Opportunity Score) and §24
(Final Hard Gate).

Two of the seven sub-scores (Setup Quality, Momentum) have no formula
anywhere in the original spec or in docs/CALCULATION_SPEC_V0.1.md §9 (which
explicitly deferred them: "الباقي يُشتق بنفس الطريقة... تُراجَع القيم
الحدّية بعد أول دفعة من Outcome Tracking"). The functions below are this
codebase's first concrete, documented V0.1 attempt — clamp/lerp based like
every other score in the system, and exactly as tunable once real outcome
data exists. Market Context, Volume/RVOL, Risk/Reward, Event Risk, and
Option Quality all reuse thresholds the spec already states explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.engines._score_utils import clamp, lerp
from app.engines.contract_engine import ContractGateResult
from app.engines.setup_engine import SetupConditions
from app.models.enums import (
    EventSeverity,
    OverallMarketRegime,
    SignalDirection,
    SignalStatus,
    TrendSlope,
)

# ---------------------------------------------------------------------------
# The seven Methodology §22 sub-scores
# ---------------------------------------------------------------------------

RVOL_WEAK_MAX = Decimal("1.20")  # Methodology §3: "< 1.20 -> ضعيف"
RVOL_ACCEPTABLE_MAX = Decimal("1.30")  # "1.20-1.30 -> مقبول", ">= 1.30 -> Confirmation"
RVOL_STRONG_MIN = Decimal("2.00")  # ">= 2.00 -> Strong"

MIN_REQUIRED_R_MULTIPLE = Decimal(2)  # Methodology §18 "R:R >= 1:2"
IDEAL_R_MULTIPLE = Decimal(3)

_EVENT_SEVERITY_SCORE = {
    EventSeverity.LOW: Decimal(100),
    EventSeverity.MEDIUM: Decimal(70),
    EventSeverity.HIGH: Decimal(30),
    EventSeverity.EXTREME: Decimal(0),
}

_MARKET_REGIME_SCORE = {
    OverallMarketRegime.SUPPORTIVE: Decimal(100),
    OverallMarketRegime.NEUTRAL: Decimal(50),
    OverallMarketRegime.RESTRICTIVE: Decimal(0),
}


def setup_quality_score(
    *,
    breakout_distance: Decimal,
    atr14: Decimal,
    min_breakout_atr_mult: Decimal,
    rvol: Decimal | None,
    rvol_threshold: Decimal,
) -> Decimal:
    """V0.1 initial formula (undefined by the original spec): blends how
    decisively the breakout cleared its minimum required distance with how
    far RVOL cleared its confirmation threshold, capped at 100 each."""
    if atr14 <= 0:
        return Decimal(0)

    min_required_distance = min_breakout_atr_mult * atr14
    distance_ratio = breakout_distance / min_required_distance if min_required_distance > 0 else Decimal(0)
    # 1x the minimum required distance -> 50, 2x or more -> 100.
    distance_score = clamp(distance_ratio * Decimal(50), Decimal(0), Decimal(100))

    if rvol is None:
        rvol_component = Decimal(0)
    else:
        rvol_component = clamp(
            lerp(rvol, rvol_threshold, Decimal(50), RVOL_STRONG_MIN, Decimal(100)), Decimal(0), Decimal(100)
        )

    return (distance_score + rvol_component) / 2


def market_context_score(overall_market_regime: OverallMarketRegime | None) -> Decimal:
    """Direct reuse of Market Context Engine's own three states — no new
    thresholds invented."""
    if overall_market_regime is None:
        return Decimal(0)
    return _MARKET_REGIME_SCORE[overall_market_regime]


def momentum_score(ema20_slope: TrendSlope | None, direction: SignalDirection) -> Decimal:
    """V0.1 initial formula. Coarse by necessity: docs/DATA_SPEC_V0.1.md §4
    only stores the categorical slope (POSITIVE/NEGATIVE/FLAT), not its
    magnitude, so this cannot be a continuous function yet — a future
    version that persists raw slope magnitude could replace this with a
    proper lerp the same way every other score here works."""
    aligned = TrendSlope.POSITIVE if direction == SignalDirection.CALL else TrendSlope.NEGATIVE
    opposite = TrendSlope.NEGATIVE if direction == SignalDirection.CALL else TrendSlope.POSITIVE

    if ema20_slope == aligned:
        return Decimal(100)
    if ema20_slope == opposite:
        return Decimal(0)
    return Decimal(50)  # FLAT, or None during EMA warm-up


def volume_rvol_score(rvol: Decimal | None) -> Decimal:
    """Reuses Methodology §3's own qualitative bands verbatim (weak /
    acceptable / confirmation-ramping-to-strong) instead of inventing new
    thresholds."""
    if rvol is None:
        return Decimal(0)  # warm-up / invalid RVOL — treated as worst case, not "unknown = neutral"
    if rvol < RVOL_WEAK_MAX:
        return Decimal(20)
    if rvol < RVOL_ACCEPTABLE_MAX:
        return Decimal(50)
    if rvol < RVOL_STRONG_MIN:
        return lerp(rvol, RVOL_ACCEPTABLE_MAX, Decimal(70), RVOL_STRONG_MIN, Decimal(100))
    return Decimal(100)


def risk_reward_score(r_multiple: Decimal) -> Decimal:
    """Methodology §18's minimum (1:2) is the floor; §19's TP2 (2R) to a
    stretch TP3 territory (3R) maps to the top of the range."""
    if r_multiple < MIN_REQUIRED_R_MULTIPLE:
        return Decimal(0)  # should already have failed the Final Hard Gate — defensive, not reachable normally
    if r_multiple >= IDEAL_R_MULTIPLE:
        return Decimal(100)
    return lerp(r_multiple, MIN_REQUIRED_R_MULTIPLE, Decimal(60), IDEAL_R_MULTIPLE, Decimal(100))


def event_risk_score(event_severity: EventSeverity | None) -> Decimal:
    """No known event (None) scores as the best case, same convention
    Setup Engine uses for its event_risk_acceptable gate."""
    if event_severity is None:
        return Decimal(100)
    return _EVENT_SEVERITY_SCORE[event_severity]


def option_quality_score(contract_score: Decimal) -> Decimal:
    """Methodology §22's "Option Quality" factor IS Phase 3's Contract
    Score — already 0-100, passed through unchanged."""
    return contract_score


# ---------------------------------------------------------------------------
# Final Opportunity Score (Methodology §22 — unambiguous weights, unlike §15)
# ---------------------------------------------------------------------------

SETUP_QUALITY_WEIGHT = Decimal("0.30")
MARKET_CONTEXT_WEIGHT = Decimal("0.20")
MOMENTUM_WEIGHT = Decimal("0.15")
VOLUME_RVOL_WEIGHT = Decimal("0.15")
RISK_REWARD_WEIGHT = Decimal("0.10")
EVENT_RISK_WEIGHT = Decimal("0.05")
OPTION_QUALITY_WEIGHT = Decimal("0.05")


@dataclass(frozen=True, slots=True)
class FinalScoreBreakdown:
    setup_quality: Decimal
    market_context: Decimal
    momentum: Decimal
    volume_rvol: Decimal
    risk_reward: Decimal
    event_risk: Decimal
    option_quality: Decimal

    @property
    def final_score(self) -> Decimal:
        return (
            SETUP_QUALITY_WEIGHT * self.setup_quality
            + MARKET_CONTEXT_WEIGHT * self.market_context
            + MOMENTUM_WEIGHT * self.momentum
            + VOLUME_RVOL_WEIGHT * self.volume_rvol
            + RISK_REWARD_WEIGHT * self.risk_reward
            + EVENT_RISK_WEIGHT * self.event_risk
            + OPTION_QUALITY_WEIGHT * self.option_quality
        )


# ---------------------------------------------------------------------------
# Final Hard Gate (Methodology §24)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FinalHardGateResult:
    """Individually inspectable — matches §24's own printed checklist
    (Market/Setup/Timing/Liquidity/Risk/R:R/Event Risk/Data Quality, each
    PASS or FAIL) so a "why NO TRADE" view can show exactly this table."""

    market: bool
    setup: bool
    timing: bool
    liquidity: bool
    risk: bool
    risk_reward: bool
    event_risk: bool
    data_quality: bool

    @property
    def passes_all(self) -> bool:
        return all(
            (
                self.market,
                self.setup,
                self.timing,
                self.liquidity,
                self.risk,
                self.risk_reward,
                self.event_risk,
                self.data_quality,
            )
        )


def evaluate_final_hard_gate(
    *,
    overall_market_regime: OverallMarketRegime | None,
    setup_conditions: SetupConditions,
    signal_status: SignalStatus,
    contract_gates: ContractGateResult,
    recommended_quantity: int,
    r_multiple: Decimal,
    data_quality_ok: bool,
) -> FinalHardGateResult:
    return FinalHardGateResult(
        market=overall_market_regime != OverallMarketRegime.RESTRICTIVE,
        setup=setup_conditions.is_full_candidate,
        timing=signal_status == SignalStatus.ENTRY,
        liquidity=contract_gates.spread_ok and contract_gates.open_interest_ok and contract_gates.volume_ok,
        risk=recommended_quantity >= 1,
        risk_reward=r_multiple >= MIN_REQUIRED_R_MULTIPLE,
        event_risk=setup_conditions.event_risk_acceptable,
        data_quality=data_quality_ok,
    )
