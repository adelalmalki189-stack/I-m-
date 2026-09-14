"""Contract Engine — Methodology §10-15 (Delta, DTE, Liquidity, OI/Volume,
IV, Contract Score) plus docs/CALCULATION_SPEC_V0.1.md §9 (score
normalization functions).

Stops exactly where docs/IMPLEMENTATION_PLAN_V0.1.md Phase 3 says to stop:
this engine ranks *contracts* for an already-decided Signal. It never
decides CALL/PUT/NO (Setup Engine, Phase 2) and never sizes a position
(Risk Engine, Phase 4).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.data_providers.schemas import OptionContractInfo, OptionQuote
from app.engines._score_utils import clamp, lerp
from app.models.enums import OptionType

# ---------------------------------------------------------------------------
# Hard Gates (Methodology §10-13)
# ---------------------------------------------------------------------------

DEFAULT_MAX_SPREAD_PCT = Decimal(5)  # §12 "Initial rule: Spread <= 5%"
DEFAULT_GOOD_SPREAD_PCT = Decimal(3)  # §12 "والأفضل: Spread <= 3%" (informational, not a gate)
DEFAULT_MIN_OPEN_INTEREST = 100  # §13
DEFAULT_MIN_VOLUME = 100  # §13
DEFAULT_MIN_ABS_DELTA = Decimal("0.40")  # §10
DEFAULT_MAX_ABS_DELTA = Decimal("0.70")  # §10
DEFAULT_MIN_DTE = 7  # §11 priority range
DEFAULT_MAX_DTE = 45  # §11 priority range
PREMIUM_SOFT_MIN = Decimal("0.50")  # §9 (Methodology Part 1) — informational only, never a gate
PREMIUM_SOFT_MAX = Decimal("4.00")


@dataclass(frozen=True, slots=True)
class ContractGateResult:
    """Individually inspectable, same rationale as Setup Engine's
    SetupConditions — each Hard Gate must be auditable on its own."""

    spread_ok: bool
    open_interest_ok: bool
    volume_ok: bool
    delta_ok: bool
    dte_ok: bool
    within_soft_premium_range: bool  # informational — never gates a rejection

    @property
    def passes_all_hard_gates(self) -> bool:
        return self.spread_ok and self.open_interest_ok and self.volume_ok and self.delta_ok and self.dte_ok


def evaluate_contract_gates(
    *,
    quote: OptionQuote,
    contract: OptionContractInfo,
    allow_0dte: bool = False,
    max_spread_pct: Decimal = DEFAULT_MAX_SPREAD_PCT,
    min_open_interest: int = DEFAULT_MIN_OPEN_INTEREST,
    min_volume: int = DEFAULT_MIN_VOLUME,
    min_abs_delta: Decimal = DEFAULT_MIN_ABS_DELTA,
    max_abs_delta: Decimal = DEFAULT_MAX_ABS_DELTA,
    min_dte: int = DEFAULT_MIN_DTE,
    max_dte: int = DEFAULT_MAX_DTE,
) -> ContractGateResult:
    """`allow_0dte` must be asserted by the caller (Setup/Risk Engine has
    already verified the stricter 0DTE conditions in Methodology §11 —
    RVOL >= 1.50, strong structure, tight spread, clear invalidation).
    This engine never grants that exception on its own."""
    spread_ok = quote.spread_pct <= max_spread_pct
    open_interest_ok = quote.open_interest >= min_open_interest
    volume_ok = quote.volume >= min_volume

    delta_ok = quote.delta is not None and min_abs_delta <= abs(quote.delta) <= max_abs_delta

    dte_ok = (min_dte <= contract.dte <= max_dte) or (contract.dte == 0 and allow_0dte)

    premium = (quote.bid + quote.ask) / 2
    within_soft_premium_range = PREMIUM_SOFT_MIN <= premium <= PREMIUM_SOFT_MAX

    return ContractGateResult(
        spread_ok=spread_ok,
        open_interest_ok=open_interest_ok,
        volume_ok=volume_ok,
        delta_ok=delta_ok,
        dte_ok=dte_ok,
        within_soft_premium_range=within_soft_premium_range,
    )


# ---------------------------------------------------------------------------
# Score normalization (docs/CALCULATION_SPEC_V0.1.md §9)
# ---------------------------------------------------------------------------

IDEAL_DELTA_MIN = Decimal("0.50")  # §10 "الأولوية المبدئية: 0.50 – 0.65"
IDEAL_DELTA_MAX = Decimal("0.65")
IDEAL_DTE_MIN = 14  # §11 midpoint preference within 7-45
IDEAL_DTE_MAX = 21
DEFAULT_OI_TARGET = 5000


def spread_score(spread_pct: Decimal, *, max_spread_pct: Decimal = DEFAULT_MAX_SPREAD_PCT) -> Decimal:
    """§9.1: 0% spread -> 100, max_spread_pct -> 0."""
    return clamp(Decimal(100) - (spread_pct / max_spread_pct) * Decimal(100), Decimal(0), Decimal(100))


def delta_score(delta: Decimal) -> Decimal:
    """§9.2 — piecewise linear, peaking on the 0.50-0.65 "ideal" band from §10."""
    d = abs(delta)
    if IDEAL_DELTA_MIN <= d <= IDEAL_DELTA_MAX:
        return Decimal(100)
    if DEFAULT_MIN_ABS_DELTA <= d < IDEAL_DELTA_MIN:
        return lerp(d, DEFAULT_MIN_ABS_DELTA, Decimal(70), IDEAL_DELTA_MIN, Decimal(100))
    if IDEAL_DELTA_MAX < d <= DEFAULT_MAX_ABS_DELTA:
        return lerp(d, IDEAL_DELTA_MAX, Decimal(100), DEFAULT_MAX_ABS_DELTA, Decimal(70))
    return Decimal(0)  # outside the Hard Gate range anyway


def dte_score(dte: int) -> Decimal:
    """§9.3 — piecewise linear, peaking on the 14-21 day "ideal" band."""
    if IDEAL_DTE_MIN <= dte <= IDEAL_DTE_MAX:
        return Decimal(100)
    if DEFAULT_MIN_DTE <= dte < IDEAL_DTE_MIN:
        return lerp(Decimal(dte), Decimal(DEFAULT_MIN_DTE), Decimal(70), Decimal(IDEAL_DTE_MIN), Decimal(100))
    if IDEAL_DTE_MAX < dte <= DEFAULT_MAX_DTE:
        return lerp(Decimal(dte), Decimal(IDEAL_DTE_MAX), Decimal(100), Decimal(DEFAULT_MAX_DTE), Decimal(70))
    return Decimal(0)  # outside the Hard Gate range anyway (0DTE scored separately by callers if allowed)


def volume_oi_score(open_interest: int, volume: int, *, oi_target: int = DEFAULT_OI_TARGET) -> Decimal:
    """§9.4 — log-scale since OI/volume can span orders of magnitude.
    Uses OI alone (matching the spec's own worked formula); volume is
    already a Hard Gate (§13) and heavily correlated with OI in practice."""
    del volume  # kept in the signature for callers/tests that want it explicit; not in the §9.4 formula itself
    oi_decimal = Decimal(max(open_interest, 1))
    target_decimal = Decimal(oi_target)
    return clamp(Decimal(100) * (oi_decimal.log10() / target_decimal.log10()), Decimal(0), Decimal(100))


def compute_iv_rank(current_iv: Decimal, historical_ivs: Sequence[Decimal]) -> Decimal | None:
    """docs/CALCULATION_SPEC_V0.1.md §9.5 — percentile rank of current IV
    against trailing (typically 252-day) history. None (not 0 or 50) when
    there is no history at all — an un-rankable IV is a data gap, not a
    "low" reading."""
    if not historical_ivs:
        return None
    count_at_or_below = sum(1 for iv in historical_ivs if iv <= current_iv)
    return Decimal(100) * Decimal(count_at_or_below) / Decimal(len(historical_ivs))


def iv_score(iv_rank: Decimal | None) -> Decimal:
    """§9.5 — a penalty function, not a rejection: high IV Rank costs
    points but never fails a Hard Gate (Methodology §14: "Not automatically
    rejected"). Missing iv_rank (no history yet) scores neutral (70) rather
    than assuming either cheap or expensive."""
    if iv_rank is None:
        return Decimal(70)
    if iv_rank <= 50:
        return Decimal(100)
    if iv_rank <= 80:
        return lerp(iv_rank, Decimal(50), Decimal(100), Decimal(80), Decimal(60))
    return lerp(clamp(iv_rank, Decimal(80), Decimal(100)), Decimal(80), Decimal(60), Decimal(100), Decimal(20))


# ---------------------------------------------------------------------------
# Composite Contract Score (Methodology §15)
# ---------------------------------------------------------------------------

# The original spec's own weight table lists SIX categories that sum to
# 100% (Liquidity 25%, Spread 20%, Delta 20%, DTE 15%, Volume/OI 10%, IV
# 10%) but only ever defines FIVE distinct scoring concepts across §9-15 —
# "Liquidity" (§13: high OI, high volume, tight spread, active trading) and
# "Volume/OI" describe the same underlying signal this module scores as
# `volume_oi_score`. Rather than inventing a sixth, unspecified formula,
# their weights are combined here (25% + 10% = 35%) onto volume_oi_score,
# keeping every other named weight untouched and traceable to the source.
SPREAD_WEIGHT = Decimal("0.20")
DELTA_WEIGHT = Decimal("0.20")
DTE_WEIGHT = Decimal("0.15")
VOLUME_OI_WEIGHT = Decimal("0.35")
IV_WEIGHT = Decimal("0.10")


def compute_contract_score(
    *,
    quote: OptionQuote,
    contract: OptionContractInfo,
    iv_rank: Decimal | None,
    oi_target: int = DEFAULT_OI_TARGET,
) -> Decimal:
    if quote.delta is None:
        raise ValueError("compute_contract_score requires a delta — caller must pass the delta Hard Gate first")

    return (
        SPREAD_WEIGHT * spread_score(quote.spread_pct)
        + DELTA_WEIGHT * delta_score(quote.delta)
        + DTE_WEIGHT * dte_score(contract.dte)
        + VOLUME_OI_WEIGHT * volume_oi_score(quote.open_interest, quote.volume, oi_target=oi_target)
        + IV_WEIGHT * iv_score(iv_rank)
    )


# ---------------------------------------------------------------------------
# Ranking (Methodology §15 — "بعد اجتياز الـHard Gates، نرتب العقود")
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContractEvaluation:
    contract: OptionContractInfo
    quote: OptionQuote
    gates: ContractGateResult
    iv_rank: Decimal | None
    score: Decimal


def evaluate_and_rank_contracts(
    candidates: Sequence[tuple[OptionContractInfo, OptionQuote]],
    *,
    historical_ivs: Sequence[Decimal] = (),
    allow_0dte: bool = False,
    oi_target: int = DEFAULT_OI_TARGET,
) -> list[ContractEvaluation]:
    """Filters to Hard-Gate passers, scores them, and returns best-first —
    exactly the "Contract A/B/C" ranking in Methodology §15."""
    evaluations: list[ContractEvaluation] = []

    for contract, quote in candidates:
        gates = evaluate_contract_gates(quote=quote, contract=contract, allow_0dte=allow_0dte)
        if not gates.passes_all_hard_gates:
            continue

        iv_rank = compute_iv_rank(quote.iv, historical_ivs) if quote.iv is not None else None
        score = compute_contract_score(quote=quote, contract=contract, iv_rank=iv_rank, oi_target=oi_target)
        evaluations.append(
            ContractEvaluation(contract=contract, quote=quote, gates=gates, iv_rank=iv_rank, score=score)
        )

    evaluations.sort(key=lambda e: e.score, reverse=True)
    return evaluations


# ---------------------------------------------------------------------------
# ATM contract selection (for docs/DATA_SPEC_V0.1.md §18 iv_history recording)
# ---------------------------------------------------------------------------


def select_atm_contract(
    contracts: Sequence[OptionContractInfo],
    underlying_price: Decimal,
    *,
    option_type: OptionType = OptionType.CALL,
    min_dte: int = DEFAULT_MIN_DTE,
) -> OptionContractInfo | None:
    """Nearest-strike contract of `option_type` at or beyond `min_dte` —
    used once daily to record iv_history (docs/CALCULATION_SPEC_V0.1.md
    §9.5 needs this so IV Rank isn't scored against zero history forever)."""
    eligible = [c for c in contracts if c.option_type == option_type and c.dte >= min_dte]
    if not eligible:
        return None
    return min(eligible, key=lambda c: (abs(c.strike - underlying_price), c.dte))
