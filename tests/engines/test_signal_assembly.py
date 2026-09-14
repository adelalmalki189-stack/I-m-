from __future__ import annotations

from decimal import Decimal

from app.engines.scoring_engine import FinalHardGateResult
from app.engines.signal_assembly import build_conditions, hard_gates_to_dict


def test_build_conditions_call_direction():
    conditions = build_conditions(
        price=Decimal(141), vwap=Decimal(112), direction_is_call=True,
        rvol=Decimal("1.5"), rvol_threshold=Decimal("1.3"),
        structure_aligned=True, spread_pct=Decimal("0.95"), max_spread_pct=Decimal(5),
    )
    by_rule = {c["rule"]: c for c in conditions}
    assert by_rule["price_above_vwap"]["value"] is True
    assert by_rule["rvol"]["passed"] is True
    assert by_rule["structure_aligned"]["value"] is True
    assert by_rule["spread_pct"]["passed"] is True


def test_build_conditions_rvol_none_is_not_passed():
    conditions = build_conditions(
        price=Decimal(100), vwap=Decimal(101), direction_is_call=False,
        rvol=None, rvol_threshold=Decimal("1.3"),
        structure_aligned=False, spread_pct=Decimal(2), max_spread_pct=Decimal(5),
    )
    by_rule = {c["rule"]: c for c in conditions}
    assert by_rule["rvol"]["passed"] is False
    assert by_rule["rvol"]["value"] is None
    assert by_rule["price_below_vwap"]["value"] is True


def test_hard_gates_to_dict_uses_rr_key_matching_api_spec():
    gate = FinalHardGateResult(
        market=True, setup=True, timing=True, liquidity=True,
        risk=True, risk_reward=False, event_risk=True, data_quality=True,
    )
    result = hard_gates_to_dict(gate)
    assert result["rr"] == "FAIL"
    assert result["market"] == "PASS"
    assert "risk_reward" not in result
