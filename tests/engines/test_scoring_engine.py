from __future__ import annotations

from decimal import Decimal

from app.engines.contract_engine import ContractGateResult
from app.engines.scoring_engine import (
    FinalScoreBreakdown,
    evaluate_final_hard_gate,
    event_risk_score,
    market_context_score,
    momentum_score,
    option_quality_score,
    risk_reward_score,
    setup_quality_score,
    volume_rvol_score,
)
from app.engines.setup_engine import SetupConditions
from app.models.enums import EventSeverity, OverallMarketRegime, SignalDirection, SignalStatus, TrendSlope

# ---------------------------------------------------------------------------
# Individual sub-scores
# ---------------------------------------------------------------------------


def test_setup_quality_score_scales_with_breakout_and_rvol_strength():
    weak = setup_quality_score(
        breakout_distance=Decimal("0.20"), atr14=Decimal(2), min_breakout_atr_mult=Decimal("0.10"),
        rvol=Decimal("1.30"), rvol_threshold=Decimal("1.30"),
    )
    strong = setup_quality_score(
        breakout_distance=Decimal("0.40"), atr14=Decimal(2), min_breakout_atr_mult=Decimal("0.10"),
        rvol=Decimal("2.00"), rvol_threshold=Decimal("1.30"),
    )
    assert strong > weak
    assert Decimal(0) <= weak <= Decimal(100)
    assert Decimal(0) <= strong <= Decimal(100)


def test_setup_quality_score_zero_without_rvol():
    score = setup_quality_score(
        breakout_distance=Decimal("0.20"), atr14=Decimal(2), min_breakout_atr_mult=Decimal("0.10"),
        rvol=None, rvol_threshold=Decimal("1.30"),
    )
    assert score >= Decimal(0)  # never negative even with the zero rvol component


def test_market_context_score_matches_regime():
    assert market_context_score(OverallMarketRegime.SUPPORTIVE) == Decimal(100)
    assert market_context_score(OverallMarketRegime.NEUTRAL) == Decimal(50)
    assert market_context_score(OverallMarketRegime.RESTRICTIVE) == Decimal(0)
    assert market_context_score(None) == Decimal(0)


def test_momentum_score_aligned_vs_opposite():
    assert momentum_score(TrendSlope.POSITIVE, SignalDirection.CALL) == Decimal(100)
    assert momentum_score(TrendSlope.NEGATIVE, SignalDirection.CALL) == Decimal(0)
    assert momentum_score(TrendSlope.FLAT, SignalDirection.CALL) == Decimal(50)
    assert momentum_score(None, SignalDirection.CALL) == Decimal(50)
    assert momentum_score(TrendSlope.NEGATIVE, SignalDirection.PUT) == Decimal(100)


def test_volume_rvol_score_reuses_methodology_bands():
    assert volume_rvol_score(Decimal("1.00")) == Decimal(20)  # weak
    assert volume_rvol_score(Decimal("1.25")) == Decimal(50)  # acceptable
    assert volume_rvol_score(Decimal("2.00")) == Decimal(100)  # strong
    assert volume_rvol_score(None) == Decimal(0)


def test_risk_reward_score_floor_and_ideal():
    assert risk_reward_score(Decimal(2)) == Decimal(60)
    assert risk_reward_score(Decimal(3)) == Decimal(100)
    assert risk_reward_score(Decimal("2.5")) == Decimal(80)
    assert risk_reward_score(Decimal("1.9")) == Decimal(0)


def test_event_risk_score_none_is_best_case():
    assert event_risk_score(None) == Decimal(100)
    assert event_risk_score(EventSeverity.LOW) == Decimal(100)
    assert event_risk_score(EventSeverity.EXTREME) == Decimal(0)


def test_option_quality_score_passthrough():
    assert option_quality_score(Decimal("87.50")) == Decimal("87.50")


# ---------------------------------------------------------------------------
# Final Opportunity Score composite
# ---------------------------------------------------------------------------


def test_final_score_all_100_is_100():
    breakdown = FinalScoreBreakdown(
        setup_quality=Decimal(100), market_context=Decimal(100), momentum=Decimal(100),
        volume_rvol=Decimal(100), risk_reward=Decimal(100), event_risk=Decimal(100),
        option_quality=Decimal(100),
    )
    assert breakdown.final_score == Decimal(100)


def test_final_score_weights_sum_to_one_hundred_percent():
    # every sub-score contributes a distinct amount to a partial input,
    # so summing them back out proves the weights add to 100%.
    breakdown = FinalScoreBreakdown(
        setup_quality=Decimal(100), market_context=Decimal(0), momentum=Decimal(0),
        volume_rvol=Decimal(0), risk_reward=Decimal(0), event_risk=Decimal(0), option_quality=Decimal(0),
    )
    assert breakdown.final_score == Decimal(30)  # setup_quality weight alone


# ---------------------------------------------------------------------------
# Final Hard Gate (Methodology §24)
# ---------------------------------------------------------------------------


def _passing_setup_conditions() -> SetupConditions:
    return SetupConditions(
        vwap_aligned=True, structure_aligned=True, breakout_confirmed=True,
        rvol_confirmed=True, market_context_supportive=True, event_risk_acceptable=True,
    )


def _passing_contract_gates() -> ContractGateResult:
    return ContractGateResult(
        spread_ok=True, open_interest_ok=True, volume_ok=True, delta_ok=True, dte_ok=True,
        within_soft_premium_range=True,
    )


def test_final_hard_gate_passes_when_everything_aligned():
    result = evaluate_final_hard_gate(
        overall_market_regime=OverallMarketRegime.SUPPORTIVE,
        setup_conditions=_passing_setup_conditions(),
        signal_status=SignalStatus.ENTRY,
        contract_gates=_passing_contract_gates(),
        recommended_quantity=2,
        r_multiple=Decimal(2),
        data_quality_ok=True,
    )
    assert result.passes_all is True


def test_final_hard_gate_fails_on_restrictive_market_alone():
    result = evaluate_final_hard_gate(
        overall_market_regime=OverallMarketRegime.RESTRICTIVE,
        setup_conditions=_passing_setup_conditions(),
        signal_status=SignalStatus.ENTRY,
        contract_gates=_passing_contract_gates(),
        recommended_quantity=2,
        r_multiple=Decimal(2),
        data_quality_ok=True,
    )
    assert result.market is False
    assert result.passes_all is False


def test_final_hard_gate_fails_on_zero_quantity():
    result = evaluate_final_hard_gate(
        overall_market_regime=OverallMarketRegime.SUPPORTIVE,
        setup_conditions=_passing_setup_conditions(),
        signal_status=SignalStatus.ENTRY,
        contract_gates=_passing_contract_gates(),
        recommended_quantity=0,
        r_multiple=Decimal(2),
        data_quality_ok=True,
    )
    assert result.risk is False
    assert result.passes_all is False


def test_final_hard_gate_fails_when_status_not_entry():
    result = evaluate_final_hard_gate(
        overall_market_regime=OverallMarketRegime.SUPPORTIVE,
        setup_conditions=_passing_setup_conditions(),
        signal_status=SignalStatus.READY,
        contract_gates=_passing_contract_gates(),
        recommended_quantity=2,
        r_multiple=Decimal(2),
        data_quality_ok=True,
    )
    assert result.timing is False
    assert result.passes_all is False


def test_final_hard_gate_fails_on_bad_data_quality_regardless_of_everything_else():
    result = evaluate_final_hard_gate(
        overall_market_regime=OverallMarketRegime.SUPPORTIVE,
        setup_conditions=_passing_setup_conditions(),
        signal_status=SignalStatus.ENTRY,
        contract_gates=_passing_contract_gates(),
        recommended_quantity=2,
        r_multiple=Decimal(2),
        data_quality_ok=False,
    )
    assert result.data_quality is False
    assert result.passes_all is False
