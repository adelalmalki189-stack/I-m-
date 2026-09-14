from __future__ import annotations

from decimal import Decimal

import pytest

from app.engines.risk_engine import (
    PortfolioRiskLimits,
    compute_premium_stop_target,
    compute_risk_plan,
    evaluate_portfolio_risk,
)
from app.models.enums import SignalDirection

# ---------------------------------------------------------------------------
# Premium translation (§8.3)
# ---------------------------------------------------------------------------


def test_premium_stop_target_call():
    result = compute_premium_stop_target(
        current_premium=Decimal("2.00"), delta=Decimal("0.50"),
        entry_price=Decimal(100), invalidation=Decimal(98), target=Decimal(104),
        direction=SignalDirection.CALL,
    )
    # risk_underlying=2, reward_underlying=4
    assert result.stop_premium == Decimal("1.00")  # 2.00 - 0.5*2
    assert result.target_premium == Decimal("4.00")  # 2.00 + 0.5*4


def test_premium_stop_floored_at_zero():
    result = compute_premium_stop_target(
        current_premium=Decimal("1.00"), delta=Decimal("0.90"),
        entry_price=Decimal(100), invalidation=Decimal(80), target=Decimal(140),
        direction=SignalDirection.CALL,
    )
    # naive calc would be 1.00 - 0.9*20 = -17 -> floored to 0
    assert result.stop_premium == Decimal(0)


def test_premium_stop_target_put_mirrors_direction():
    result = compute_premium_stop_target(
        current_premium=Decimal("2.00"), delta=Decimal("-0.50"),
        entry_price=Decimal(100), invalidation=Decimal(102), target=Decimal(94),
        direction=SignalDirection.PUT,
    )
    # risk_underlying = invalidation-entry = 2; reward_underlying = entry-target = 6
    assert result.stop_premium == Decimal("1.00")  # 2.00 - 0.5*2
    assert result.target_premium == Decimal("5.00")  # 2.00 + 0.5*6


# ---------------------------------------------------------------------------
# RiskPlan sizing
# ---------------------------------------------------------------------------


def test_risk_plan_basic_sizing():
    plan = compute_risk_plan(
        account_size=Decimal(2000), risk_percent=Decimal(1),
        entry_premium=Decimal("2.00"), stop_premium=Decimal("1.00"), target_premium=Decimal("4.00"),
    )
    # max_risk = 20; risk_per_contract = (2.00-1.00)*100 = 100 -> quantity=0
    assert plan.max_risk == Decimal(20)
    assert plan.risk_per_contract == Decimal(100)
    assert plan.recommended_quantity == 0
    assert plan.max_loss == Decimal(0)


def test_risk_plan_never_rounds_up_to_one_contract():
    # docs/TESTING_SPEC_V0.1.md §3: account too small for even one
    # contract's risk -> 0, never rounded up.
    plan = compute_risk_plan(
        account_size=Decimal(50), risk_percent=Decimal(1),
        entry_premium=Decimal("2.00"), stop_premium=Decimal("1.00"), target_premium=Decimal("4.00"),
    )
    assert plan.max_risk == Decimal("0.5")
    assert plan.recommended_quantity == 0


def test_risk_plan_sizes_multiple_contracts_when_account_allows():
    plan = compute_risk_plan(
        account_size=Decimal(20000), risk_percent=Decimal(1),
        entry_premium=Decimal("2.00"), stop_premium=Decimal("1.00"), target_premium=Decimal("4.00"),
    )
    # max_risk = 200; risk_per_contract = 100 -> quantity = 2
    assert plan.recommended_quantity == 2
    assert plan.max_loss == Decimal(200)
    assert plan.potential_profit == Decimal(400)  # reward_per_contract=200 * 2
    assert plan.r_multiple == Decimal(2)


def test_risk_plan_rejects_invalid_stop_above_entry():
    with pytest.raises(ValueError):
        compute_risk_plan(
            account_size=Decimal(2000), risk_percent=Decimal(1),
            entry_premium=Decimal("1.00"), stop_premium=Decimal("1.50"), target_premium=Decimal("2.00"),
        )


# ---------------------------------------------------------------------------
# Portfolio-level risk (docs/OPERATIONAL_SPEC_V0.1.md §5)
# ---------------------------------------------------------------------------


DEFAULT_LIMITS = PortfolioRiskLimits(
    max_concurrent_signals=3, max_concurrent_per_sector=2,
    daily_max_loss_pct=Decimal(3), max_total_deployed_risk_pct=Decimal(5),
)


def test_portfolio_risk_passes_when_under_all_limits():
    check = evaluate_portfolio_risk(
        open_signals_count=1, open_signals_in_sector_count=0, daily_realized_pnl=Decimal(0),
        account_size=Decimal(10000), new_trade_risk=Decimal(100), sum_open_risk=Decimal(100),
        limits=DEFAULT_LIMITS,
    )
    assert check.passes_all is True


def test_portfolio_risk_blocks_at_max_concurrent_signals():
    check = evaluate_portfolio_risk(
        open_signals_count=3, open_signals_in_sector_count=0, daily_realized_pnl=Decimal(0),
        account_size=Decimal(10000), new_trade_risk=Decimal(100), sum_open_risk=Decimal(100),
        limits=DEFAULT_LIMITS,
    )
    assert check.max_concurrent_ok is False
    assert check.passes_all is False


def test_portfolio_risk_blocks_at_daily_loss_limit_exactly():
    # account=10000, daily_max_loss_pct=3% -> max_daily_loss=300.
    # realized_pnl == -300 hits the limit exactly -> blocked (not "still ok").
    check = evaluate_portfolio_risk(
        open_signals_count=0, open_signals_in_sector_count=0, daily_realized_pnl=Decimal(-300),
        account_size=Decimal(10000), new_trade_risk=Decimal(0), sum_open_risk=Decimal(0),
        limits=DEFAULT_LIMITS,
    )
    assert check.daily_loss_limit_ok is False


def test_portfolio_risk_blocks_when_new_trade_would_exceed_deployed_cap():
    # max_deployed_risk = 10000*5% = 500. sum_open=450 + new=100 = 550 > 500.
    check = evaluate_portfolio_risk(
        open_signals_count=1, open_signals_in_sector_count=0, daily_realized_pnl=Decimal(0),
        account_size=Decimal(10000), new_trade_risk=Decimal(100), sum_open_risk=Decimal(450),
        limits=DEFAULT_LIMITS,
    )
    assert check.max_deployed_risk_ok is False
