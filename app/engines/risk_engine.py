"""Risk Engine — Methodology §16-19 (Risk Management, Stop Loss, R:R,
Profit Targets) plus docs/CALCULATION_SPEC_V0.1.md §8.3 (delta-approximated
premium translation) and docs/OPERATIONAL_SPEC_V0.1.md §5 (portfolio-level
risk, added on top of the original per-trade-only design).

Consumes Setup Engine's underlying-level invalidation/target and the
selected contract's delta (Phase 3) to produce the premium-side numbers a
user actually trades against — this is the "known limitation" flagged in
CALCULATION_SPEC_V0.1.md §8.3: a linear delta approximation, no gamma/theta.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.models.enums import SignalDirection

DEFAULT_CONTRACT_MULTIPLIER = 100


@dataclass(frozen=True, slots=True)
class PremiumTargets:
    stop_premium: Decimal
    target_premium: Decimal


def compute_premium_stop_target(
    *,
    current_premium: Decimal,
    delta: Decimal,
    entry_price: Decimal,
    invalidation: Decimal,
    target: Decimal,
    direction: SignalDirection,
) -> PremiumTargets:
    """docs/CALCULATION_SPEC_V0.1.md §8.3:

        Stop_premium   ≈ current_premium - |delta| * Risk_underlying
        Target_premium ≈ current_premium + |delta| * (Target_underlying - entry_price)

    `stop_premium` is floored at 0 — a premium can go to worthless, never
    negative, however large the approximated move is."""
    abs_delta = abs(delta)

    if direction == SignalDirection.CALL:
        risk_underlying = entry_price - invalidation
        reward_underlying = target - entry_price
    else:
        risk_underlying = invalidation - entry_price
        reward_underlying = entry_price - target

    stop_premium = max(current_premium - abs_delta * risk_underlying, Decimal(0))
    target_premium = current_premium + abs_delta * reward_underlying

    return PremiumTargets(stop_premium=stop_premium, target_premium=target_premium)


@dataclass(frozen=True, slots=True)
class RiskPlanResult:
    risk_per_contract: Decimal
    recommended_quantity: int
    max_risk: Decimal
    max_loss: Decimal
    potential_profit: Decimal
    r_multiple: Decimal


def compute_risk_plan(
    *,
    account_size: Decimal,
    risk_percent: Decimal,
    entry_premium: Decimal,
    stop_premium: Decimal,
    target_premium: Decimal,
    contract_multiplier: int = DEFAULT_CONTRACT_MULTIPLIER,
) -> RiskPlanResult:
    """docs/DATA_SPEC_V0.1.md §13. `recommended_quantity` is a floor
    division — docs/TESTING_SPEC_V0.1.md §3: an account too small for even
    one contract's risk must yield 0 (NO TRADE), never rounded up to 1."""
    if stop_premium >= entry_premium:
        raise ValueError("stop_premium must be below entry_premium for a CALL-shaped long-premium risk")

    risk_per_contract = (entry_premium - stop_premium) * contract_multiplier
    reward_per_contract = (target_premium - entry_premium) * contract_multiplier
    max_risk = account_size * risk_percent / Decimal(100)

    recommended_quantity = int(max_risk // risk_per_contract) if risk_per_contract > 0 else 0

    max_loss = Decimal(recommended_quantity) * risk_per_contract
    potential_profit = Decimal(recommended_quantity) * reward_per_contract
    r_multiple = reward_per_contract / risk_per_contract if risk_per_contract > 0 else Decimal(0)

    return RiskPlanResult(
        risk_per_contract=risk_per_contract,
        recommended_quantity=recommended_quantity,
        max_risk=max_risk,
        max_loss=max_loss,
        potential_profit=potential_profit,
        r_multiple=r_multiple,
    )


# ---------------------------------------------------------------------------
# Portfolio-level risk (docs/OPERATIONAL_SPEC_V0.1.md §5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PortfolioRiskLimits:
    """Pure-data mirror of the portfolio_risk_config ORM row — kept
    decoupled from SQLAlchemy so this engine has no database dependency."""

    max_concurrent_signals: int
    max_concurrent_per_sector: int
    daily_max_loss_pct: Decimal
    max_total_deployed_risk_pct: Decimal


@dataclass(frozen=True, slots=True)
class PortfolioRiskCheck:
    max_concurrent_ok: bool
    max_concurrent_per_sector_ok: bool
    daily_loss_limit_ok: bool
    max_deployed_risk_ok: bool

    @property
    def passes_all(self) -> bool:
        return (
            self.max_concurrent_ok
            and self.max_concurrent_per_sector_ok
            and self.daily_loss_limit_ok
            and self.max_deployed_risk_ok
        )


def evaluate_portfolio_risk(
    *,
    open_signals_count: int,
    open_signals_in_sector_count: int,
    daily_realized_pnl: Decimal,
    account_size: Decimal,
    new_trade_risk: Decimal,
    sum_open_risk: Decimal,
    limits: PortfolioRiskLimits,
) -> PortfolioRiskCheck:
    """docs/OPERATIONAL_SPEC_V0.1.md §5 — every check is a `<` / `<=`
    against a configured limit, never a silent skip. A losing day that
    exactly hits the limit (not exceeds it) already blocks new ENTRYs for
    the rest of the session, matching the spec's own worked rule."""
    max_daily_loss = account_size * limits.daily_max_loss_pct / Decimal(100)
    max_deployed_risk = account_size * limits.max_total_deployed_risk_pct / Decimal(100)

    return PortfolioRiskCheck(
        max_concurrent_ok=open_signals_count < limits.max_concurrent_signals,
        max_concurrent_per_sector_ok=open_signals_in_sector_count < limits.max_concurrent_per_sector,
        daily_loss_limit_ok=daily_realized_pnl > -max_daily_loss,
        max_deployed_risk_ok=(sum_open_risk + new_trade_risk) <= max_deployed_risk,
    )
