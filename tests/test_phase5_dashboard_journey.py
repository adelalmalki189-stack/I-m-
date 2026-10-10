"""Phase 5 completion criterion (docs/IMPLEMENTATION_PLAN_V0.1.md):

"مستخدم يفتح Dashboard، يرى فرصة، يضغط 'WHY THIS TRADE؟' ويرى كل الأسباب
الخام، ويسجل صفقة يدوياً بعد تنفيذها في IBKR."

Reuses Phase 4's full trade-proposal computation (breakout_session golden
fixture through Feature Engine -> ... -> Final Hard Gate), persists it as
a real Signal with `conditions`/`hard_gates` populated via
signal_assembly.py, then drives the exact user journey end to end through
the real HTTP API: the dashboard's own opportunities list, why-this-trade
panel, and manual trade log.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

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
from app.engines.signal_assembly import build_conditions, hard_gates_to_dict
from app.engines.timing_engine import determine_status
from app.main import app
from app.models.enums import MarketStructureState, SignalDirection, SignalStatus, TrendSlope
from app.models.instruments import Instrument
from app.models.risk import RiskPlan
from app.models.signals import Signal
from app.models.strategy_parameters import StrategyParameters
from tests.engines.factories_options import make_contract, make_quote
from tests.fixtures.golden.breakout_session import BREAKOUT_INDEX, build_baseline_sessions, build_current_session

CALC_VERSION = "v0.1.0-dashboard-journey"
FROZEN_RESISTANCE_LEVEL = Decimal(130)
ENTRY_INDEX = 22


async def _seed_passing_signal(db_session) -> Signal:
    candles = build_current_session()
    rows = compute_technical_features_for_session(
        candles, previous_session_candles=None,
        baseline_sessions_for_rvol=build_baseline_sessions(), calc_version=CALC_VERSION,
    )
    atr_frozen = rows[BREAKOUT_INDEX].atr14
    zone = compute_entry_zone(level=FROZEN_RESISTANCE_LEVEL, atr14=atr_frozen, direction=SignalDirection.CALL)

    overall_market_regime = combine_overall_regime(
        spy_regime=classify_market_regime(
            price=Decimal(105), vwap=Decimal(100), ema20=Decimal(102), ema50=Decimal(100),
            ema20_slope=TrendSlope.POSITIVE, market_structure=MarketStructureState.BULLISH,
        ),
        qqq_regime=classify_market_regime(
            price=Decimal(205), vwap=Decimal(200), ema20=Decimal(202), ema50=Decimal(200),
            ema20_slope=TrendSlope.POSITIVE, market_structure=MarketStructureState.BULLISH,
        ),
        vix_regime=classify_vix_regime(Decimal(15)),
    )

    row = rows[ENTRY_INDEX]
    conditions_result = evaluate_setup_conditions(
        direction=SignalDirection.CALL, price=candles[ENTRY_INDEX].close, vwap=row.vwap,
        close=candles[ENTRY_INDEX].close, market_structure=row.market_structure,
        level=FROZEN_RESISTANCE_LEVEL, rvol=row.rvol, atr14=atr_frozen,
        overall_market_regime=overall_market_regime, event_severity=None,
    )
    status = determine_status(
        direction=SignalDirection.CALL, is_pre_breakout_ok=conditions_result.pre_breakout_ok,
        breakout_confirmed=conditions_result.breakout_confirmed, breakout_level=FROZEN_RESISTANCE_LEVEL,
        close=candles[ENTRY_INDEX].close, entry_zone=zone, previous_status=SignalStatus.WAIT,
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
        account_size=Decimal(100000), risk_percent=Decimal(1), entry_premium=current_premium,
        stop_premium=premium_targets.stop_premium, target_premium=premium_targets.target_premium,
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
        overall_market_regime=overall_market_regime, setup_conditions=conditions_result,
        signal_status=status, contract_gates=contract_gates,
        recommended_quantity=risk_plan.recommended_quantity, r_multiple=risk_plan.r_multiple,
        data_quality_ok=True,
    )
    assert final_gate.passes_all is True  # sanity: this fixture is meant to be a clean ENTRY

    conditions_json = build_conditions(
        price=candles[ENTRY_INDEX].close, vwap=row.vwap, direction_is_call=True,
        rvol=row.rvol, rvol_threshold=Decimal("1.30"), structure_aligned=conditions_result.structure_aligned,
        spread_pct=quote.spread_pct, max_spread_pct=Decimal(5),
    )

    db_session.add(Instrument(symbol=candles[0].symbol, exchange="SMART", is_active=True))
    db_session.add(StrategyParameters(version=CALC_VERSION, parameters={}, effective_from=candles[0].timestamp))
    await db_session.flush()

    signal = Signal(
        timestamp=candles[BREAKOUT_INDEX].timestamp, symbol=candles[0].symbol, direction=SignalDirection.CALL,
        status=status, setup_score=breakdown.setup_quality, contract_score=contract_score,
        final_score=breakdown.final_score, entry_zone_low=zone.acceptable_min, entry_zone_high=zone.acceptable_max,
        invalidation=invalidation, target=target, risk_reward=risk_plan.r_multiple,
        conditions=conditions_json, hard_gates=hard_gates_to_dict(final_gate), calc_version=CALC_VERSION,
    )
    db_session.add(signal)
    await db_session.flush()

    db_session.add(
        RiskPlan(
            signal_id=signal.signal_id, account_size=Decimal(100000), risk_percent=Decimal(1),
            max_risk=risk_plan.max_risk, entry_price=entry_price, stop_price=invalidation, target_price=target,
            risk_per_contract=risk_plan.risk_per_contract, recommended_quantity=risk_plan.recommended_quantity,
            max_loss=risk_plan.max_loss, potential_profit=risk_plan.potential_profit,
            r_multiple=risk_plan.r_multiple,
        )
    )
    await db_session.commit()
    return signal


async def test_dashboard_page_loads():
    with TestClient(app) as client:
        response = client.get("/dashboard/")
        requests_response = client.get("/dashboard/requests.html")
        comparison_response = client.get("/dashboard/comparison.html")
        clients_response = client.get("/dashboard/clients.html")
        policies_response = client.get("/dashboard/policies.html")
        claims_response = client.get("/dashboard/claims.html")
        collections_response = client.get("/dashboard/collections.html")
        insurers_response = client.get("/dashboard/insurers.html")
        reports_response = client.get("/dashboard/reports.html")
        settings_response = client.get("/dashboard/settings.html")
        portal_response = client.get("/dashboard/portal.html")
    assert response.status_code == 200
    assert "BUD | بُعد للوساطة التأمينية" in response.text
    assert "رحلة طلبات التأمين" in response.text
    assert requests_response.status_code == 200
    assert "صندوق الطلبات الرقمية" in requests_response.text
    assert "مساعد بُعد الذكي" in requests_response.text
    assert comparison_response.status_code == 200
    assert "مقارنة التغطيات" in comparison_response.text
    assert "أفضل توازن" in comparison_response.text
    assert clients_response.status_code == 200
    assert "ملفات العملاء" in clients_response.text
    assert "اقتراح متابعة آلي" in clients_response.text
    assert policies_response.status_code == 200
    assert "الوثائق والتجديدات" in policies_response.text
    assert "أولوية التجديد المقترحة" in policies_response.text
    assert claims_response.status_code == 200
    assert "متابعة المطالبات" in claims_response.text
    assert "مساعدة فرز المطالبة" in claims_response.text
    assert collections_response.status_code == 200
    assert "التحصيلات والتسويات" in collections_response.text
    assert "تسويات عمولات الشركات" in collections_response.text
    assert insurers_response.status_code == 200
    assert "شبكة شركات التأمين" in insurers_response.text
    assert "مراقبة جودة الربط" in insurers_response.text
    assert reports_response.status_code == 200
    assert "التقارير والتحليلات" in reports_response.text
    assert "رؤى تشغيلية" in reports_response.text
    assert settings_response.status_code == 200
    assert "الإعدادات والصلاحيات" in settings_response.text
    assert "ضوابط التوصيات الذكية" in settings_response.text
    assert portal_response.status_code == 200
    assert "اطلب تأمينك من بُعد" in portal_response.text
    assert "إرسال طلب المعاينة" in portal_response.text


async def test_full_user_journey_see_opportunity_why_and_log_trade(db_session):
    signal = await _seed_passing_signal(db_session)

    with TestClient(app) as client:
        # 1. User opens the dashboard and it lists the opportunity.
        opportunities = client.get("/signals", params={"status": "ENTRY,READY"})
        assert opportunities.status_code == 200
        symbols = [s["symbol"] for s in opportunities.json()]
        assert signal.symbol in symbols
        listed = next(s for s in opportunities.json() if s["signal_id"] == signal.signal_id)
        assert listed["status"] == "ENTRY"

        # 2. User clicks "WHY THIS TRADE?" and sees the raw reasons.
        why = client.get(f"/signals/{signal.signal_id}/why")
        assert why.status_code == 200
        why_body = why.json()
        assert why_body["hard_gates"]["market"] == "PASS"
        assert why_body["hard_gates"]["rr"] == "PASS"
        assert any(c["rule"] == "price_above_vwap" and c["value"] is True for c in why_body["conditions"])
        assert any(c["rule"] == "rvol" and c["passed"] is True for c in why_body["conditions"])

        # 3. User checks the contract that was recommended.
        detail = client.get(f"/signals/{signal.signal_id}")
        assert detail.status_code == 200
        assert Decimal(detail.json()["risk_reward"]) > Decimal(0)

        # 4. User manually executed in IBKR and logs the trade.
        log_response = client.post(
            "/trades",
            json={
                "signal_id": signal.signal_id,
                "entry_time": "2026-03-02T14:22:00+00:00",
                "entry_price": "2.09",
                "quantity": 4,
            },
        )
        assert log_response.status_code == 201

        journal = client.get("/trades")
        assert journal.status_code == 200
        assert any(t["signal_id"] == signal.signal_id for t in journal.json())
