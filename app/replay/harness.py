"""Replay harness — walks synthetic multi-day candles through the exact
production engine chain (docs/BACKTEST_PLAN_V0.1.md §2's core rule: "الـ
backtest يستدعي نفس الدوال بالضبط المستخدمة في الإنتاج").

Two inputs Replay Mode cannot get from a synthetic single-symbol candle
generator are simplified here, both per the approximation §3 explicitly
allows (documented, not hidden):
  - `overall_market_regime` is one fixed caller-supplied input for the
    whole run instead of derived from synthetic SPY/QQQ/VIX series — no
    live orchestrator exists yet in this codebase to decide that per
    candle anyway.
  - The option contract is one fixed synthetic quote per signal (mid-range
    delta, tight spread, ample liquidity) rather than a real chain — this
    still genuinely exercises the Contract Engine's Hard Gates and Score
    (it does not bypass them), it just never varies contract quality.

Everything else — Feature Engine, Setup Engine, Timing Engine, Risk
Engine, Scoring Engine, Final Hard Gate — is the identical function the
API (and, once wired, live ingestion) calls.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from app.data_providers.schemas import Candle, OptionContractInfo, OptionQuote
from app.engines.contract_engine import compute_contract_score, evaluate_contract_gates
from app.engines.feature_engine.pipeline import compute_technical_features_for_session
from app.engines.feature_engine.session_levels import compute_opening_range
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
    DEFAULT_MIN_BREAKOUT_ATR_MULT,
    DEFAULT_RVOL_THRESHOLD,
    compute_entry_zone,
    compute_invalidation,
    compute_target,
    evaluate_setup_conditions,
    opposite_direction_extreme,
    select_level,
)
from app.engines.timing_engine import determine_status
from app.models.enums import DataStatus, OptionType, OverallMarketRegime, SignalDirection, SignalStatus

DEFAULT_ACCOUNT_SIZE = Decimal(50000)
DEFAULT_RISK_PERCENT = Decimal(1)
DEFAULT_MIN_BASELINE_DAYS = 20
SYNTHETIC_CONTRACT_DTE = 21
FORWARD_RETURN_BARS = 10


@dataclass(frozen=True, slots=True)
class ReplaySignalResult:
    symbol: str
    direction: SignalDirection
    day_index: int
    candle_index: int
    entry_price: Decimal
    invalidation: Decimal
    target: Decimal
    final_score: Decimal
    passes_final_gate: bool
    recommended_quantity: int
    r_multiple: Decimal
    forward_return_10bars: Decimal | None


def _synthetic_contract(
    *, entry_price: Decimal, direction: SignalDirection
) -> tuple[OptionContractInfo, OptionQuote]:
    """One fixed, comfortably-mid-range contract per signal — see module
    docstring. Strike is set at-the-money so the fixed 0.55 delta is at
    least directionally plausible for the entry price it is paired with."""
    option_type = OptionType.CALL if direction == SignalDirection.CALL else OptionType.PUT
    delta = Decimal("0.55") if direction == SignalDirection.CALL else Decimal("-0.55")
    contract = OptionContractInfo(
        contract_id="REPLAY_SYNTH",
        symbol="REPLAY",
        option_type=option_type,
        strike=entry_price.quantize(Decimal(1)),
        expiration=date(2026, 1, 1),
        dte=SYNTHETIC_CONTRACT_DTE,
    )
    quote = OptionQuote(
        contract_id="REPLAY_SYNTH",
        timestamp=datetime.now(UTC),
        bid=Decimal("1.95"),
        ask=Decimal("2.00"),
        last=Decimal("1.98"),
        volume=1000,
        open_interest=5000,
        iv=Decimal("0.35"),
        delta=delta,
        gamma=None,
        theta=None,
        vega=None,
        source="SYNTHETIC",
        data_status=DataStatus.VALID,
    )
    return contract, quote


def _forward_return(
    session: Sequence[Candle], index: int, *, bars: int, direction: SignalDirection
) -> Decimal | None:
    """Diagnostic-only proxy for MFE/MAE (docs/BACKTEST_PLAN_V0.1.md §5) —
    a real Outcome Tracker (Phase 6) needs a persisted Signal and 5m
    candles table; this harness only checks rule sanity (does a higher
    score trend toward a better outcome, even loosely), so a plain forward
    price change is enough. None when there aren't `bars` candles left in
    the session — never a fabricated partial-window value."""
    target_index = index + bars
    if target_index >= len(session):
        return None
    entry = session[index].close
    later = session[target_index].close
    change = (later - entry) / entry
    return change if direction == SignalDirection.CALL else -change


def replay_symbol(
    *,
    symbol: str,
    daily_sessions: Sequence[Sequence[Candle]],
    calc_version: str,
    overall_market_regime: OverallMarketRegime,
    direction: SignalDirection,
    account_size: Decimal = DEFAULT_ACCOUNT_SIZE,
    risk_percent: Decimal = DEFAULT_RISK_PERCENT,
    min_baseline_days: int = DEFAULT_MIN_BASELINE_DAYS,
) -> list[ReplaySignalResult]:
    """Walks every day >= min_baseline_days forward, re-running the
    production Feature -> Setup -> Timing -> Contract -> Risk -> Scoring
    chain candle-by-candle (docs/BACKTEST_PLAN_V0.1.md §4), and records one
    ReplaySignalResult per fresh ENTRY transition.

    A "level" (resistance for CALL / support for PUT) is re-selected every
    candle via the real select_level while status is WAIT (no live level
    committed to yet), then held fixed once pre-breakout conditions align
    (READY/ENTRY) so breakout tracking and invalidation stay anchored to
    the level that was actually used to trigger them — exactly how a
    single live Signal instance behaves. INVALIDATED resets tracking so a
    fresh attempt can form later the same day, per Methodology §8 ("A
    fresh Signal must be created for a new attempt").
    """
    results: list[ReplaySignalResult] = []

    for day_index in range(min_baseline_days, len(daily_sessions)):
        session = daily_sessions[day_index]
        previous_session = daily_sessions[day_index - 1]
        baseline_sessions = daily_sessions[day_index - min_baseline_days : day_index]
        prior_candles_for_continuity = [c for prev in daily_sessions[:day_index] for c in prev]

        rows = compute_technical_features_for_session(
            session,
            previous_session_candles=previous_session,
            baseline_sessions_for_rvol=baseline_sessions,
            calc_version=calc_version,
            prior_candles_for_continuity=prior_candles_for_continuity,
        )
        opening_range = compute_opening_range(session)
        or_extreme = (
            (opening_range[0] if direction == SignalDirection.CALL else opening_range[1])
            if opening_range is not None
            else None
        )

        previous_status: SignalStatus | None = None
        frozen_level: Decimal | None = None

        for i, candle in enumerate(session):
            row = rows[i]

            if previous_status in (None, SignalStatus.WAIT):
                level_candidate = row.swing_high if direction == SignalDirection.CALL else row.swing_low
                pd_extreme = row.pdh if direction == SignalDirection.CALL else row.pdl
                selected = select_level(
                    price=candle.close,
                    swing_extreme=level_candidate,
                    prior_day_extreme=pd_extreme,
                    opening_range_extreme=or_extreme,
                    atr14=row.atr14,
                )
                if selected is not None:
                    frozen_level = selected

            if frozen_level is None or row.atr14 is None:
                previous_status = SignalStatus.WAIT
                continue

            conditions = evaluate_setup_conditions(
                direction=direction,
                price=candle.close,
                vwap=row.vwap,
                close=candle.close,
                market_structure=row.market_structure,
                level=frozen_level,
                rvol=row.rvol,
                atr14=row.atr14,
                overall_market_regime=overall_market_regime,
                event_severity=None,
            )
            zone = compute_entry_zone(level=frozen_level, atr14=row.atr14, direction=direction)
            status = determine_status(
                direction=direction,
                is_pre_breakout_ok=conditions.pre_breakout_ok,
                breakout_confirmed=conditions.breakout_confirmed,
                breakout_level=frozen_level,
                close=candle.close,
                entry_zone=zone,
                previous_status=previous_status,
            )

            fresh_entry = status == SignalStatus.ENTRY and previous_status != SignalStatus.ENTRY
            active_level = frozen_level
            if status == SignalStatus.INVALIDATED:
                frozen_level = None
            previous_status = status

            if not fresh_entry:
                continue

            entry_price = candle.close
            swing_extreme = opposite_direction_extreme(row.swing_high, row.swing_low, direction)
            invalidation = compute_invalidation(
                level=active_level, atr14=row.atr14, swing_extreme=swing_extreme, direction=direction
            )
            try:
                target = compute_target(entry_price=entry_price, invalidation=invalidation, direction=direction)
            except ValueError:
                continue  # invalidation landed on the wrong side this candle -- skip, not a crash

            contract, quote = _synthetic_contract(entry_price=entry_price, direction=direction)
            contract_gates = evaluate_contract_gates(quote=quote, contract=contract)
            contract_score = compute_contract_score(quote=quote, contract=contract, iv_rank=None)

            current_premium = (quote.bid + quote.ask) / 2
            assert quote.delta is not None
            premium_targets = compute_premium_stop_target(
                current_premium=current_premium,
                delta=quote.delta,
                entry_price=entry_price,
                invalidation=invalidation,
                target=target,
                direction=direction,
            )
            if premium_targets.stop_premium >= current_premium:
                continue  # degenerate premium math this candle -- skip, not a crash

            risk_plan = compute_risk_plan(
                account_size=account_size,
                risk_percent=risk_percent,
                entry_premium=current_premium,
                stop_premium=premium_targets.stop_premium,
                target_premium=premium_targets.target_premium,
            )

            breakout_distance = abs(entry_price - active_level)
            breakdown = FinalScoreBreakdown(
                setup_quality=setup_quality_score(
                    breakout_distance=breakout_distance,
                    atr14=row.atr14,
                    min_breakout_atr_mult=DEFAULT_MIN_BREAKOUT_ATR_MULT,
                    rvol=row.rvol,
                    rvol_threshold=DEFAULT_RVOL_THRESHOLD,
                ),
                market_context=market_context_score(overall_market_regime),
                momentum=momentum_score(row.ema20_slope, direction),
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

            results.append(
                ReplaySignalResult(
                    symbol=symbol,
                    direction=direction,
                    day_index=day_index,
                    candle_index=i,
                    entry_price=entry_price,
                    invalidation=invalidation,
                    target=target,
                    final_score=breakdown.final_score,
                    passes_final_gate=final_gate.passes_all,
                    recommended_quantity=risk_plan.recommended_quantity,
                    r_multiple=risk_plan.r_multiple,
                    forward_return_10bars=_forward_return(
                        session, i, bars=FORWARD_RETURN_BARS, direction=direction
                    ),
                )
            )

    return results
