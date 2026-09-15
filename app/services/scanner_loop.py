"""Live Scanner Loop — the orchestrator every previous phase's docstrings
deferred to ("a future live scanner loop", "Phase 6+ scanner loop", etc.):
ties the DataProvider, the Feature/Market-Context/Setup/Timing/Contract/
Risk/Scoring engines, the database, the Redis market-context cache, the
Alerting Service, and the WS channel into one running process.

Every calculation below calls the exact same production function tested
throughout Phases 1-4 (docs/BACKTEST_PLAN_V0.1.md §2's own diagram calls
this the "Live Data Feed" side of the architecture, mirrored by
app/replay/harness.py's "Historical DB (Replay Mode)" side — the two paths
intentionally converge on the same engine calls). This module is pure
orchestration/plumbing; it introduces no new trading logic of its own.

Two inputs this loop cannot get for free are handled with the same
"never fabricate" discipline as every engine before it:
  - `overall_market_regime` starts as None and only ever gets set once
    both context symbols (SPY/QQQ) have a real (non-warm-up) Feature Engine
    reading — a tracked symbol just sits at WAIT until then, exactly as it
    would during any other genuine data-quality gap.
  - Event risk is read from `market_events` for (symbol, today) if any row
    exists there; with none, event risk is treated as unknown-but-low per
    the same convention app/engines/setup_engine.py already documents
    (`event_risk_acceptable=True` when `event_severity is None`).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.api.ws import publish_market_context_update
from app.core.database import get_session
from app.core.market_context_cache import RedisLike, set_cached_market_context
from app.data_providers.base import DataProvider
from app.data_providers.schemas import Candle
from app.engines.contract_engine import (
    DEFAULT_MAX_DTE,
    DEFAULT_MAX_SPREAD_PCT,
    DEFAULT_MIN_DTE,
    evaluate_and_rank_contracts,
)
from app.engines.feature_engine.pipeline import compute_technical_features_for_session
from app.engines.feature_engine.session_levels import compute_opening_range
from app.engines.market_context_engine import (
    classify_market_regime,
    classify_vix_regime,
    combine_overall_regime,
)
from app.engines.risk_engine import (
    PortfolioRiskLimits,
    compute_premium_stop_target,
    compute_risk_plan,
    evaluate_portfolio_risk,
)
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
from app.engines.signal_assembly import build_conditions, hard_gates_to_dict
from app.engines.timing_engine import determine_status
from app.models.candles import Candle5m
from app.models.enums import (
    EventSeverity,
    MarketRegime,
    OptionType,
    OverallMarketRegime,
    SignalDirection,
    SignalStatus,
)
from app.models.instruments import Instrument
from app.models.iv_history import IVHistory
from app.models.market_events import MarketEvent
from app.models.risk import RiskPlan
from app.models.signal_snapshots import SignalSnapshot
from app.models.signals import Signal
from app.models.technical_features import TechnicalFeatures
from app.services.alerting_service import notify_signal_entry
from app.services.candle_history import (
    get_baseline_sessions,
    get_continuity_candles,
    get_previous_session_candles,
    get_session_candles,
    to_candle_dto,
)
from app.services.portfolio_risk import get_or_create_portfolio_config, get_portfolio_risk_inputs
from app.services.signal_lifecycle import SignalTerminalError, apply_signal_status_transition

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = (EventSeverity.LOW, EventSeverity.MEDIUM, EventSeverity.HIGH, EventSeverity.EXTREME)


@dataclass
class _DirectionState:
    """One symbol/direction's live tracking state — the same lifecycle
    app/replay/harness.py's per-day loop follows, just persisted across
    real candles instead of returned as a list."""

    tracking_date: date
    frozen_level: Decimal | None = None
    previous_status: SignalStatus | None = None
    current_signal_id: str | None = None
    risk_plan_created: bool = False


@dataclass
class _ContextReading:
    row: Any  # FeatureRow, kept loosely typed here to avoid a heavy import cycle
    price: Decimal


class ScannerLoop:
    def __init__(
        self,
        *,
        provider: DataProvider,
        redis_client: RedisLike,
        tracked_symbols: list[str],
        context_symbols: tuple[str, str] = ("SPY", "QQQ"),
        calc_version: str,
        min_baseline_days: int = 20,
        continuity_lookback_sessions: int = 30,
        app_state: Any = None,
    ) -> None:
        self._provider = provider
        self._redis_client = redis_client
        self._tracked_symbols = list(tracked_symbols)
        self._context_symbols = context_symbols
        self._calc_version = calc_version
        self._min_baseline_days = min_baseline_days
        self._continuity_lookback_sessions = continuity_lookback_sessions
        self._app_state = app_state

        self._context: dict[str, _ContextReading] = {}
        self._latest_overall_regime: OverallMarketRegime | None = None
        self._latest_spy_regime: MarketRegime | None = None
        self._latest_qqq_regime: MarketRegime | None = None
        self._latest_vix_value: Decimal | None = None
        self._tracking: dict[tuple[str, SignalDirection], _DirectionState] = {}

    async def start(self) -> None:
        """Ensures every symbol this loop cares about has an `instruments`
        row, then subscribes to live 5m candles for all of them. Does not
        block — the caller (app/scripts/run_live_scanner.py) owns the
        process lifecycle, matching app/scripts/run_candle_ingestion.py's
        existing pattern."""
        all_symbols = sorted({*self._tracked_symbols, *self._context_symbols})
        async with get_session() as session:
            for symbol in all_symbols:
                await session.merge(Instrument(symbol=symbol, exchange="SMART", is_active=True))
            await session.commit()

        for symbol in all_symbols:
            await self._provider.subscribe_candles_5m(symbol, self.handle_candle)

    async def handle_candle(self, candle: Candle) -> None:
        """The loop's real unit of work — one closed 5m candle. Exposed
        directly (not only reachable via a live subscription) so tests can
        drive it deterministically with synthetic candles."""
        if not candle.is_complete:
            return  # never process a still-forming bar

        await self._persist_candle(candle)

        day = candle.timestamp.date()
        computed = await self._recompute_and_persist_latest_feature_row(candle.symbol, day)
        if computed is None:
            return
        row, current_session_candles = computed

        if candle.symbol in self._context_symbols:
            self._context[candle.symbol] = _ContextReading(row=row, price=candle.close)
            await self._maybe_update_market_context()

        if candle.symbol in self._tracked_symbols:
            for direction in (SignalDirection.CALL, SignalDirection.PUT):
                await self._evaluate_symbol_direction(
                    candle.symbol, direction, candle, row, current_session_candles, day
                )

        if self._app_state is not None:
            self._app_state.last_data_update_at = datetime.now(UTC)

    # -- Candle/feature persistence -----------------------------------

    async def _persist_candle(self, candle: Candle) -> None:
        async with get_session() as session:
            await session.merge(
                Candle5m(
                    symbol=candle.symbol, timestamp=candle.timestamp, open=candle.open, high=candle.high,
                    low=candle.low, close=candle.close, volume=candle.volume, vwap=candle.vwap,
                    is_complete=True,
                )
            )
            await session.commit()

    async def _recompute_and_persist_latest_feature_row(self, symbol: str, day: date):
        async with get_session() as session:
            current_rows = await get_session_candles(session, symbol, day)
            if not current_rows:
                return None
            current_candles = [to_candle_dto(r) for r in current_rows]

            previous_rows = await get_previous_session_candles(session, symbol, day)
            previous_candles = [to_candle_dto(r) for r in previous_rows] if previous_rows else None

            baseline_rows = await get_baseline_sessions(
                session, symbol, day, min_valid_days=self._min_baseline_days
            )
            baseline_sessions = [[to_candle_dto(r) for r in s] for s in baseline_rows]

            continuity_rows = await get_continuity_candles(
                session, symbol, day, lookback_sessions=self._continuity_lookback_sessions
            )
            continuity_candles = [to_candle_dto(r) for r in continuity_rows]

            feature_rows = compute_technical_features_for_session(
                current_candles,
                previous_session_candles=previous_candles,
                baseline_sessions_for_rvol=baseline_sessions,
                calc_version=self._calc_version,
                prior_candles_for_continuity=continuity_candles,
            )
            latest = feature_rows[-1]

            await session.merge(
                TechnicalFeatures(
                    symbol=latest.symbol, timestamp=latest.timestamp, ema20=latest.ema20, ema50=latest.ema50,
                    ema20_slope=latest.ema20_slope, rvol=latest.rvol, atr14=latest.atr14, pdh=latest.pdh,
                    pdl=latest.pdl, day_high=latest.day_high, day_low=latest.day_low,
                    swing_high=latest.swing_high, swing_low=latest.swing_low,
                    swing_confirmed_at=latest.swing_confirmed_at, market_structure=latest.market_structure,
                    calc_version=latest.calc_version,
                )
            )
            await session.commit()
            return latest, current_candles

    # -- Market context --------------------------------------------------

    async def _maybe_update_market_context(self) -> None:
        spy_symbol, qqq_symbol = self._context_symbols
        spy = self._context.get(spy_symbol)
        qqq = self._context.get(qqq_symbol)
        if spy is None or qqq is None:
            return

        spy_regime = classify_market_regime(
            price=spy.price, vwap=spy.row.vwap, ema20=spy.row.ema20, ema50=spy.row.ema50,
            ema20_slope=spy.row.ema20_slope, market_structure=spy.row.market_structure,
        )
        qqq_regime = classify_market_regime(
            price=qqq.price, vwap=qqq.row.vwap, ema20=qqq.row.ema20, ema50=qqq.row.ema50,
            ema20_slope=qqq.row.ema20_slope, market_structure=qqq.row.market_structure,
        )
        if spy_regime is None or qqq_regime is None:
            return  # still in EMA warm-up -- never publish a fabricated regime

        market_data = await self._provider.get_market_context()
        vix_regime = classify_vix_regime(market_data.vix_value)
        overall = combine_overall_regime(spy_regime=spy_regime, qqq_regime=qqq_regime, vix_regime=vix_regime)

        self._latest_overall_regime = overall
        self._latest_spy_regime = spy_regime
        self._latest_qqq_regime = qqq_regime
        self._latest_vix_value = market_data.vix_value

        payload = {
            "timestamp": market_data.timestamp.isoformat(),
            "spy": {"price": str(spy.price), "regime": spy_regime.value},
            "qqq": {"price": str(qqq.price), "regime": qqq_regime.value},
            "vix": {"value": str(market_data.vix_value), "regime": vix_regime.value},
            "overall_market_regime": overall.value,
        }
        await set_cached_market_context(self._redis_client, payload)
        await publish_market_context_update(overall.value)

    # -- Event risk --------------------------------------------------------

    async def _current_event_severity(self, session, symbol: str, day: date) -> EventSeverity | None:
        day_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        day_end = datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=UTC)
        result = await session.execute(
            select(MarketEvent.severity).where(
                MarketEvent.event_time >= day_start,
                MarketEvent.event_time <= day_end,
                (MarketEvent.symbol == symbol) | (MarketEvent.symbol.is_(None)),
            )
        )
        severities = list(result.scalars().all())
        if not severities:
            return None
        return max(severities, key=_SEVERITY_ORDER.index)

    # -- Setup/Timing per symbol/direction --------------------------------

    async def _evaluate_symbol_direction(
        self, symbol: str, direction: SignalDirection, candle: Candle, row, current_session_candles, day: date
    ) -> None:
        key = (symbol, direction)
        state = self._tracking.get(key)
        if state is None or state.tracking_date != day:
            state = _DirectionState(tracking_date=day)
            self._tracking[key] = state

        if state.previous_status in (None, SignalStatus.WAIT):
            level_candidate = row.swing_high if direction == SignalDirection.CALL else row.swing_low
            pd_extreme = row.pdh if direction == SignalDirection.CALL else row.pdl
            opening_range = compute_opening_range(current_session_candles)
            or_extreme = (
                (opening_range[0] if direction == SignalDirection.CALL else opening_range[1])
                if opening_range is not None
                else None
            )
            selected = select_level(
                price=candle.close, swing_extreme=level_candidate, prior_day_extreme=pd_extreme,
                opening_range_extreme=or_extreme, atr14=row.atr14,
            )
            if selected is not None:
                state.frozen_level = selected

        if state.frozen_level is None or row.atr14 is None:
            state.previous_status = SignalStatus.WAIT
            return

        async with get_session() as session:
            event_severity = await self._current_event_severity(session, symbol, day)

            conditions = evaluate_setup_conditions(
                direction=direction, price=candle.close, vwap=row.vwap, close=candle.close,
                market_structure=row.market_structure, level=state.frozen_level, rvol=row.rvol,
                atr14=row.atr14, overall_market_regime=self._latest_overall_regime,
                event_severity=event_severity,
            )
            zone = compute_entry_zone(level=state.frozen_level, atr14=row.atr14, direction=direction)
            status = determine_status(
                direction=direction, is_pre_breakout_ok=conditions.pre_breakout_ok,
                breakout_confirmed=conditions.breakout_confirmed, breakout_level=state.frozen_level,
                close=candle.close, entry_zone=zone, previous_status=state.previous_status,
            )
            if state.risk_plan_created and status == SignalStatus.WAIT:
                # determine_status legitimately returns WAIT once price runs past the
                # acceptable entry zone (docs/CALCULATION_SPEC_V0.1.md §7: "beyond
                # max_extension is a miss, not a late entry") -- correct for a signal
                # that never fired, but this one already reached ENTRY, was scored,
                # got its RiskPlan, and alerted the user. That fact doesn't un-happen
                # because price moved on; only a genuine INVALIDATED (broken back
                # through the level, or lost pre-breakout conditions) should end it.
                status = SignalStatus.ENTRY
            swing_extreme = opposite_direction_extreme(row.swing_high, row.swing_low, direction)
            invalidation = compute_invalidation(
                level=state.frozen_level, atr14=row.atr14, swing_extreme=swing_extreme, direction=direction
            )

            signal = await self._persist_signal_state(
                session, state, symbol, direction, candle, status, zone, invalidation, event_severity
            )

            if signal is not None and status == SignalStatus.ENTRY and not state.risk_plan_created:
                try:
                    target = compute_target(
                        entry_price=candle.close, invalidation=invalidation, direction=direction
                    )
                except ValueError:
                    target = None
                if target is not None:
                    created = await self._handle_entry(
                        session, signal, symbol, direction, candle, row, conditions,
                        state.frozen_level, invalidation, target, event_severity,
                    )
                    state.risk_plan_created = created

        state.previous_status = status
        if status == SignalStatus.INVALIDATED:
            state.frozen_level = None
            state.previous_status = None
            state.current_signal_id = None
            state.risk_plan_created = False

    async def _persist_signal_state(
        self, session, state: _DirectionState, symbol: str, direction: SignalDirection, candle: Candle,
        status: SignalStatus, zone, invalidation: Decimal, event_severity: EventSeverity | None,
    ) -> Signal | None:
        if state.current_signal_id is None:
            if status == SignalStatus.WAIT:
                return None  # nothing worth persisting yet -- avoid flooding signals with WAIT rows
            signal = Signal(
                timestamp=candle.timestamp, symbol=symbol, direction=direction, status=status,
                entry_zone_low=zone.acceptable_min, entry_zone_high=zone.acceptable_max,
                invalidation=invalidation, event_risk=event_severity, calc_version=self._calc_version,
            )
            session.add(signal)
            await session.commit()
            state.current_signal_id = signal.signal_id
            if status == SignalStatus.ENTRY:
                await notify_signal_entry(session, signal)
            return signal

        signal = await session.get(Signal, state.current_signal_id)
        if signal is None:
            return None
        signal.entry_zone_low = zone.acceptable_min
        signal.entry_zone_high = zone.acceptable_max
        signal.invalidation = invalidation
        signal.event_risk = event_severity
        if status != state.previous_status:
            try:
                await apply_signal_status_transition(session, signal, status)
            except SignalTerminalError:
                logger.warning("attempted to move terminal signal %s to %s", signal.signal_id, status)
        else:
            await session.commit()
        return signal

    async def _handle_entry(
        self, session, signal: Signal, symbol: str, direction: SignalDirection, candle: Candle, row,
        conditions, frozen_level: Decimal, invalidation: Decimal, target: Decimal,
        event_severity: EventSeverity | None,
    ) -> bool:
        entry_price = candle.close
        option_type = OptionType.CALL if direction == SignalDirection.CALL else OptionType.PUT

        chain = await self._provider.get_option_chain(symbol)
        dte_filtered = [
            c for c in chain if c.option_type == option_type and DEFAULT_MIN_DTE <= c.dte <= DEFAULT_MAX_DTE
        ]
        candidates = []
        for contract in dte_filtered:
            quote = await self._provider.get_option_quote(contract.contract_id)
            candidates.append((contract, quote))

        iv_history_result = await session.execute(
            select(IVHistory.atm_iv).where(IVHistory.symbol == symbol).order_by(IVHistory.date.desc()).limit(252)
        )
        historical_ivs = [Decimal(str(v)) for v in iv_history_result.scalars().all()]

        ranked = evaluate_and_rank_contracts(candidates, historical_ivs=historical_ivs)
        if not ranked:
            return False
        best = ranked[0]
        assert best.quote.delta is not None  # guaranteed by the delta Hard Gate already passing

        current_premium = (best.quote.bid + best.quote.ask) / 2
        premium_targets = compute_premium_stop_target(
            current_premium=current_premium, delta=best.quote.delta, entry_price=entry_price,
            invalidation=invalidation, target=target, direction=direction,
        )
        if premium_targets.stop_premium >= current_premium:
            return False

        config = await get_or_create_portfolio_config(session)
        risk_plan_result = compute_risk_plan(
            account_size=config.account_size, risk_percent=config.risk_percent, entry_premium=current_premium,
            stop_premium=premium_targets.stop_premium, target_premium=premium_targets.target_premium,
        )

        portfolio_inputs = await get_portfolio_risk_inputs(session, symbol)
        portfolio_limits = PortfolioRiskLimits(
            max_concurrent_signals=config.max_concurrent_signals,
            max_concurrent_per_sector=config.max_concurrent_per_sector,
            daily_max_loss_pct=config.daily_max_loss_pct,
            max_total_deployed_risk_pct=config.max_total_deployed_risk_pct,
        )
        portfolio_check = evaluate_portfolio_risk(
            open_signals_count=portfolio_inputs.open_signals_count,
            open_signals_in_sector_count=portfolio_inputs.open_signals_in_sector_count,
            daily_realized_pnl=portfolio_inputs.daily_realized_pnl, account_size=config.account_size,
            new_trade_risk=risk_plan_result.max_risk, sum_open_risk=portfolio_inputs.sum_open_risk,
            limits=portfolio_limits,
        )
        # A portfolio-level breach forces NO TRADE for this opportunity without
        # inventing a status the schema doesn't have (docs/OPERATIONAL_SPEC_V0.1.md
        # §5) -- the Final Hard Gate's own `risk` check already treats
        # recommended_quantity == 0 as a fail.
        recommended_quantity = risk_plan_result.recommended_quantity if portfolio_check.passes_all else 0

        breakdown = FinalScoreBreakdown(
            setup_quality=setup_quality_score(
                breakout_distance=abs(entry_price - frozen_level), atr14=row.atr14,
                min_breakout_atr_mult=DEFAULT_MIN_BREAKOUT_ATR_MULT, rvol=row.rvol,
                rvol_threshold=DEFAULT_RVOL_THRESHOLD,
            ),
            market_context=market_context_score(self._latest_overall_regime),
            momentum=momentum_score(row.ema20_slope, direction),
            volume_rvol=volume_rvol_score(row.rvol),
            risk_reward=risk_reward_score(risk_plan_result.r_multiple),
            event_risk=event_risk_score(event_severity),
            option_quality=option_quality_score(best.score),
        )
        final_gate = evaluate_final_hard_gate(
            overall_market_regime=self._latest_overall_regime, setup_conditions=conditions,
            signal_status=SignalStatus.ENTRY, contract_gates=best.gates,
            recommended_quantity=recommended_quantity, r_multiple=risk_plan_result.r_multiple,
            data_quality_ok=True,
        )

        signal.setup_score = breakdown.setup_quality
        signal.contract_score = best.score
        signal.final_score = breakdown.final_score
        signal.risk_reward = risk_plan_result.r_multiple
        signal.target = target
        signal.conditions = build_conditions(
            price=entry_price, vwap=row.vwap, direction_is_call=(direction == SignalDirection.CALL),
            rvol=row.rvol, rvol_threshold=DEFAULT_RVOL_THRESHOLD, structure_aligned=conditions.structure_aligned,
            spread_pct=best.quote.spread_pct, max_spread_pct=DEFAULT_MAX_SPREAD_PCT,
        )
        signal.hard_gates = hard_gates_to_dict(final_gate)

        session.add(
            SignalSnapshot(
                signal_id=signal.signal_id, underlying_price=entry_price, vwap=row.vwap, ema20=row.ema20,
                ema50=row.ema50, rvol=row.rvol,
                spy_state=self._latest_spy_regime.value if self._latest_spy_regime else None,
                qqq_state=self._latest_qqq_regime.value if self._latest_qqq_regime else None,
                vix_value=self._latest_vix_value,
                contract_id=best.contract.contract_id, strike=best.contract.strike,
                expiration=best.contract.expiration, dte=best.contract.dte,
                bid=best.quote.bid, ask=best.quote.ask, last=best.quote.last, volume=best.quote.volume,
                oi=best.quote.open_interest, iv=best.quote.iv, delta=best.quote.delta,
                calc_version=self._calc_version,
            )
        )
        session.add(
            RiskPlan(
                signal_id=signal.signal_id, account_size=config.account_size, risk_percent=config.risk_percent,
                max_risk=risk_plan_result.max_risk, entry_price=entry_price, stop_price=invalidation,
                target_price=target, risk_per_contract=risk_plan_result.risk_per_contract,
                recommended_quantity=recommended_quantity, max_loss=risk_plan_result.max_loss,
                potential_profit=risk_plan_result.potential_profit, r_multiple=risk_plan_result.r_multiple,
            )
        )
        await session.commit()
        return True
