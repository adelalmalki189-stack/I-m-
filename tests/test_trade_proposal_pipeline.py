"""Phase 4 completion criterion (docs/IMPLEMENTATION_PLAN_V0.1.md):

"النموذج الكامل في §24 من المواصفة الأصلية (NVDA CALL Score 93/100...)
يظهر فعلياً من طرف لطرف على بيانات Replay."

Runs the full chain — Feature Engine -> Market Context -> Setup -> Timing
-> Contract Engine -> Risk Engine -> Scoring Engine -> Final Hard Gate —
over the same breakout_session golden fixture used in Phase 2, then
persists the RiskPlan and the Signal's score fields through the ORM.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.engines.contract_engine import compute_contract_score, evaluate_contract_gates
from app.engines.feature_engine.pipeline import compute_technical_features_for_session
from app.engines.market_context_engine import (
    classify_market_regime,
    classify_vix_regime,
    combine_overall_regime,
)
from app.engines.risk_engine import compute_premium_stop_target, compute_risk_plan
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
from app.engines.setup_engine import (
    compute_entry_zone,
    compute_invalidation,
    compute_target,
    evaluate_setup_conditions,
)
from app.engines.timing_engine import determine_status
from app.models.enums import MarketStructureState, SignalDirection, SignalStatus, TrendSlope
from app.models.instruments import Instrument
from app.models.risk import RiskPlan
from app.models.signals import Signal
from app.models.strategy_parameters import StrategyParameters
from tests.engines.factories_options import make_contract, make_quote
from tests.fixtures.golden.breakout_session import BREAKOUT_INDEX, build_baseline_sessions, build_current_session

CALC_VERSION = "v0.1.0-trade-proposal"
FROZEN_RESISTANCE_LEVEL = Decimal(130)
ENTRY_INDEX = 22  # the candle where Phase 2's status sequence reaches ENTRY


def _build_trade_proposal():
    """Runs the entire chain once and returns every intermediate result —
    shared by both tests below so the pure-computation assertions and the
    persistence test can't silently drift apart."""
    candles = build_current_session()
    rows = compute_technical_features_for_session(
        candles,
        previous_session_candles=None,
        baseline_sessions_for_rvol=build_baseline_sessions(),
        calc_version=CALC_VERSION,
    )

    atr_frozen = rows[BREAKOUT_INDEX].atr14
    assert atr_frozen is not None
    zone = compute_entry_zone(level=FROZEN_RESISTANCE_LEVEL, atr14=atr_frozen, direction=SignalDirection.CALL)

    spy_regime = classify_market_regime(
        price=Decimal(105), vwap=Decimal(100), ema20=Decimal(102), ema50=Decimal(100),
        ema20_slope=TrendSlope.POSITIVE, market_structure=MarketStructureState.BULLISH,
    )
    qqq_regime = classify_market_regime(
        price=Decimal(205), vwap=Decimal(200), ema20=Decimal(202), ema50=Decimal(200),
        ema20_slope=TrendSlope.POSITIVE, market_structure=MarketStructureState.BULLISH,
    )
    overall_market_regime = combine_overall_regime(
        spy_regime=spy_regime, qqq_regime=qqq_regime, vix_regime=classify_vix_regime(Decimal(15))
    )

    row = rows[ENTRY_INDEX]
    conditions = evaluate_setup_conditions(
        direction=SignalDirection.CALL,
        price=candles[ENTRY_INDEX].close,
        vwap=row.vwap,
        close=candles[ENTRY_INDEX].close,
        market_structure=row.market_structure,
        level=FROZEN_RESISTANCE_LEVEL,
        rvol=row.rvol,
        atr14=atr_frozen,
        overall_market_regime=overall_market_regime,
        event_severity=None,
    )
    status = determine_status(
        direction=SignalDirection.CALL,
        is_pre_breakout_ok=conditions.pre_breakout_ok,
        breakout_confirmed=conditions.breakout_confirmed,
        breakout_level=FROZEN_RESISTANCE_LEVEL,
        close=candles[ENTRY_INDEX].close,
        entry_zone=zone,
        previous_status=SignalStatus.WAIT,  # matches the WAIT->WAIT->ENTRY sequence from Phase 2's test
    )

    entry_price = candles[ENTRY_INDEX].close
    invalidation = compute_invalidation(
        level=FROZEN_RESISTANCE_LEVEL, atr14=atr_frozen,
        swing_extreme=rows[BREAKOUT_INDEX].swing_low, direction=SignalDirection.CALL,
    )
    target = compute_target(entry_price=entry_price, invalidation=invalidation, direction=SignalDirection.CALL)

    contract = make_contract(contract_id="A", dte=18)
    quote = make_quote(contract_id="A", bid=2.08, ask=2.10, delta=0.58, open_interest=8000, volume=2000)
    contract_gates = evaluate_contract_gates(quote=quote, contract=contract)
    contract_score = compute_contract_score(quote=quote, contract=contract, iv_rank=None)

    current_premium = (quote.bid + quote.ask) / 2
    premium_targets = compute_premium_stop_target(
        current_premium=current_premium, delta=quote.delta, entry_price=entry_price,
        invalidation=invalidation, target=target, direction=SignalDirection.CALL,
    )
    risk_plan = compute_risk_plan(
        account_size=Decimal(100000), risk_percent=Decimal(1),
        entry_premium=current_premium, stop_premium=premium_targets.stop_premium,
        target_premium=premium_targets.target_premium,
    )

    breakout_distance = candles[BREAKOUT_INDEX].close - FROZEN_RESISTANCE_LEVEL
    breakdown = FinalScoreBreakdown(
        setup_quality=setup_quality_score(
            breakout_distance=breakout_distance, atr14=atr_frozen, min_breakout_atr_mult=Decimal("0.10"),
            rvol=row.rvol, rvol_threshold=Decimal("1.30"),
        ),
        market_context=market_context_score(overall_market_regime),
        momentum=momentum_score(row.ema20_slope, SignalDirection.CALL),
        volume_rvol=volume_rvol_score(row.rvol),
        risk_reward=risk_reward_score(risk_plan.r_multiple),
        event_risk=event_risk_score(None),
        option_quality=option_quality_score(contract_score),
    )

    final_gate = evaluate_final_hard_gate(
        overall_market_regime=overall_market_regime,
        setup_conditions=conditions,
        signal_status=status,
        contract_gates=contract_gates,
        recommended_quantity=risk_plan.recommended_quantity,
        r_multiple=risk_plan.r_multiple,
        data_quality_ok=True,
    )

    return {
        "candles": candles,
        "status": status,
        "entry_price": entry_price,
        "invalidation": invalidation,
        "target": target,
        "contract_score": contract_score,
        "risk_plan": risk_plan,
        "breakdown": breakdown,
        "final_gate": final_gate,
    }


def test_trade_proposal_end_to_end_passes_final_hard_gate():
    proposal = _build_trade_proposal()

    assert proposal["status"] == SignalStatus.ENTRY
    assert proposal["entry_price"] == Decimal(141)
    assert proposal["invalidation"] == Decimal(106)
    assert proposal["target"] == Decimal(211)  # 141 + 2*(141-106)

    plan = proposal["risk_plan"]
    assert plan.recommended_quantity == 4
    assert plan.risk_per_contract == Decimal("209.00")

    # This is the model's own version of the original spec's worked example
    # (NVDA CALL Score 93/100, §24) — a full ENTRY-quality setup lands in
    # the low-to-mid 90s once every factor is genuinely strong.
    final_score = proposal["breakdown"].final_score
    assert Decimal(90) <= final_score <= Decimal(95)

    assert proposal["final_gate"].passes_all is True


async def test_trade_proposal_persists_risk_plan_and_signal_scores(db_session):
    proposal = _build_trade_proposal()
    candles = proposal["candles"]

    db_session.add(Instrument(symbol=candles[0].symbol, exchange="SMART", is_active=True))
    db_session.add(
        StrategyParameters(version=CALC_VERSION, parameters={}, effective_from=candles[0].timestamp)
    )
    await db_session.flush()

    signal = Signal(
        timestamp=candles[BREAKOUT_INDEX].timestamp,
        symbol=candles[0].symbol,
        direction=SignalDirection.CALL,
        status=proposal["status"],
        contract_score=proposal["contract_score"],
        final_score=proposal["breakdown"].final_score,
        entry_zone_low=proposal["entry_price"],
        invalidation=proposal["invalidation"],
        target=proposal["target"],
        risk_reward=proposal["risk_plan"].r_multiple,
        calc_version=CALC_VERSION,
    )
    db_session.add(signal)
    await db_session.flush()

    plan = proposal["risk_plan"]
    db_session.add(
        RiskPlan(
            signal_id=signal.signal_id,
            account_size=Decimal(100000),
            risk_percent=Decimal(1),
            max_risk=plan.max_risk,
            entry_price=proposal["entry_price"],
            stop_price=proposal["invalidation"],
            target_price=proposal["target"],
            risk_per_contract=plan.risk_per_contract,
            recommended_quantity=plan.recommended_quantity,
            max_loss=plan.max_loss,
            potential_profit=plan.potential_profit,
            r_multiple=plan.r_multiple,
        )
    )
    await db_session.commit()

    persisted_signal = await db_session.get(Signal, signal.signal_id)
    assert persisted_signal is not None
    assert persisted_signal.final_score == proposal["breakdown"].final_score

    result = await db_session.execute(select(RiskPlan).where(RiskPlan.signal_id == signal.signal_id))
    persisted_plan = result.scalar_one()
    assert persisted_plan.recommended_quantity == 4
