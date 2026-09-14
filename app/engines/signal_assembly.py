"""Bridges the pure engines (Setup, Contract, Risk, Scoring) to the
`conditions`/`hard_gates` JSON persisted on a Signal row
(app/models/signals.py), which docs/API_SPEC_V0.1.md §3's "why this trade"
endpoint serves verbatim. A future live scanner loop (Phase 6+) is the
real caller of `build_conditions`/`hard_gates_to_dict`; this module has no
DB or DataProvider dependency of its own, consistent with every other
engine in this package.
"""
from __future__ import annotations

from decimal import Decimal

from app.engines.scoring_engine import FinalHardGateResult


def build_conditions(
    *,
    price: Decimal,
    vwap: Decimal,
    direction_is_call: bool,
    rvol: Decimal | None,
    rvol_threshold: Decimal,
    structure_aligned: bool,
    spread_pct: Decimal,
    max_spread_pct: Decimal,
) -> list[dict]:
    """Methodology §6/§12 condition-by-condition, in the exact shape
    docs/API_SPEC_V0.1.md §3 documents (rule/value/threshold/passed)."""
    return [
        {
            "rule": "price_above_vwap" if direction_is_call else "price_below_vwap",
            "value": (price > vwap) if direction_is_call else (price < vwap),
        },
        {
            "rule": "rvol",
            "value": float(rvol) if rvol is not None else None,
            "threshold": float(rvol_threshold),
            "passed": rvol is not None and rvol >= rvol_threshold,
        },
        {"rule": "structure_aligned", "value": structure_aligned},
        {
            "rule": "spread_pct",
            "value": float(spread_pct),
            "threshold": float(max_spread_pct),
            "passed": spread_pct <= max_spread_pct,
        },
    ]


def hard_gates_to_dict(gate: FinalHardGateResult) -> dict[str, str]:
    """Methodology §24's exact eight-row checklist, PASS/FAIL per row —
    key names match docs/API_SPEC_V0.1.md §3's worked example verbatim
    (including "rr" for risk_reward)."""
    return {
        "market": "PASS" if gate.market else "FAIL",
        "setup": "PASS" if gate.setup else "FAIL",
        "timing": "PASS" if gate.timing else "FAIL",
        "liquidity": "PASS" if gate.liquidity else "FAIL",
        "risk": "PASS" if gate.risk else "FAIL",
        "rr": "PASS" if gate.risk_reward else "FAIL",
        "event_risk": "PASS" if gate.event_risk else "FAIL",
        "data_quality": "PASS" if gate.data_quality else "FAIL",
    }
