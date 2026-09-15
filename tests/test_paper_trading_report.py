"""Tests for the Paper Trading report (app/scripts/run_paper_trading_report.py)
— Phase 8's diagnostic tool for an actual IBKR Paper run. Seeds real DB rows
(Signal/SignalSnapshot/RiskPlan/Outcome/Trade) rather than a Replay, since
the point of this script is to summarize genuinely-persisted paper-trading
data.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.enums import ExitReason, SignalDirection, SignalStatus
from app.models.instruments import Instrument
from app.models.outcomes import Outcome
from app.models.risk import RiskPlan
from app.models.signal_snapshots import SignalSnapshot
from app.models.signals import Signal
from app.models.strategy_parameters import StrategyParameters
from app.models.trades import Trade
from app.scripts.run_paper_trading_report import build_paper_trading_report, format_report

CALC_VERSION = "v0.1.0-paper-trading-test"
PERIOD_START = datetime(2026, 3, 1, tzinfo=UTC)
PERIOD_END = datetime(2026, 3, 15, tzinfo=UTC)


async def _seed_instrument_and_params(db_session) -> None:
    db_session.add(Instrument(symbol="NVDA", exchange="SMART", is_active=True))
    db_session.add(StrategyParameters(version=CALC_VERSION, parameters={}, effective_from=PERIOD_START))
    await db_session.commit()


async def _seed_entry_signal(
    db_session, *, timestamp: datetime, final_score: Decimal, recommended_quantity: int,
    entry_price: Decimal, final_mfe: Decimal | None,
) -> Signal:
    signal = Signal(
        timestamp=timestamp, symbol="NVDA", direction=SignalDirection.CALL, status=SignalStatus.ENTRY,
        final_score=final_score, calc_version=CALC_VERSION,
    )
    db_session.add(signal)
    await db_session.flush()

    db_session.add(
        SignalSnapshot(
            signal_id=signal.signal_id, underlying_price=entry_price, vwap=entry_price - Decimal(1),
            calc_version=CALC_VERSION,
        )
    )
    db_session.add(
        RiskPlan(
            signal_id=signal.signal_id, account_size=Decimal(50000), risk_percent=Decimal(1),
            max_risk=Decimal(500), entry_price=Decimal(2), stop_price=Decimal("1.5"), target_price=Decimal(3),
            risk_per_contract=Decimal(50), recommended_quantity=recommended_quantity,
            max_loss=Decimal(500) if recommended_quantity else Decimal(0),
            potential_profit=Decimal(1000), r_multiple=Decimal(2),
        )
    )
    if final_mfe is not None:
        db_session.add(
            Outcome(
                signal_id=signal.signal_id, offset_minutes=60,
                underlying_price_at_offset=entry_price + final_mfe, mfe=final_mfe, mae=Decimal(0),
            )
        )
    await db_session.commit()
    return signal


async def test_empty_period_returns_a_report_with_no_signals_and_no_crash(db_session):
    await _seed_instrument_and_params(db_session)

    report = await build_paper_trading_report(db_session, period_start=PERIOD_START, period_end=PERIOD_END)

    assert report.total_signals == 0
    assert report.entry_signals == 0
    assert report.mean_final_score is None
    assert report.final_gate_pass_rate == 0.0
    assert report.mean_final_outcome_pct is None
    assert report.score_outcome_correlation is None
    assert report.logged_trades == 0
    assert report.trade_win_rate is None
    assert report.total_realized_pnl is None
    # Formatting must not crash on an all-empty report either.
    assert "Paper Trading Report" in format_report(report)


async def test_signals_outside_the_period_are_excluded(db_session):
    await _seed_instrument_and_params(db_session)
    await _seed_entry_signal(
        db_session, timestamp=PERIOD_START - timedelta(days=5), final_score=Decimal(90),
        recommended_quantity=2, entry_price=Decimal(100), final_mfe=Decimal(2),
    )

    report = await build_paper_trading_report(db_session, period_start=PERIOD_START, period_end=PERIOD_END)

    assert report.total_signals == 0


async def test_final_gate_pass_rate_reflects_recommended_quantity(db_session):
    await _seed_instrument_and_params(db_session)
    await _seed_entry_signal(
        db_session, timestamp=PERIOD_START + timedelta(days=1), final_score=Decimal(92),
        recommended_quantity=3, entry_price=Decimal(100), final_mfe=Decimal(2),
    )
    await _seed_entry_signal(
        db_session, timestamp=PERIOD_START + timedelta(days=2), final_score=Decimal(88),
        recommended_quantity=0, entry_price=Decimal(150), final_mfe=Decimal(1),
    )

    report = await build_paper_trading_report(db_session, period_start=PERIOD_START, period_end=PERIOD_END)

    assert report.total_signals == 2
    assert report.mean_final_score == Decimal(90)
    assert report.final_gate_pass_rate == 0.5


async def test_mean_final_outcome_pct_normalizes_by_entry_price(db_session):
    await _seed_instrument_and_params(db_session)
    # +2 on a 100 entry -> 2%; +4 on a 200 entry -> 2% as well.
    await _seed_entry_signal(
        db_session, timestamp=PERIOD_START + timedelta(days=1), final_score=Decimal(90),
        recommended_quantity=1, entry_price=Decimal(100), final_mfe=Decimal(2),
    )
    await _seed_entry_signal(
        db_session, timestamp=PERIOD_START + timedelta(days=2), final_score=Decimal(90),
        recommended_quantity=1, entry_price=Decimal(200), final_mfe=Decimal(4),
    )

    report = await build_paper_trading_report(db_session, period_start=PERIOD_START, period_end=PERIOD_END)

    assert report.mean_final_outcome_pct == Decimal("0.02")


async def test_signal_without_an_outcome_row_is_excluded_from_the_outcome_average(db_session):
    await _seed_instrument_and_params(db_session)
    await _seed_entry_signal(
        db_session, timestamp=PERIOD_START + timedelta(days=1), final_score=Decimal(90),
        recommended_quantity=1, entry_price=Decimal(100), final_mfe=None,
    )

    report = await build_paper_trading_report(db_session, period_start=PERIOD_START, period_end=PERIOD_END)

    assert report.total_signals == 1
    assert report.mean_final_outcome_pct is None
    assert report.score_outcome_correlation is None


async def test_score_outcome_correlation_is_perfectly_positive_for_matched_data(db_session):
    await _seed_instrument_and_params(db_session)
    score_mfe_pairs = [(Decimal(80), Decimal(1)), (Decimal(85), Decimal(2)), (Decimal(90), Decimal(3))]
    for i, (score, mfe) in enumerate(score_mfe_pairs):
        await _seed_entry_signal(
            db_session, timestamp=PERIOD_START + timedelta(days=i + 1), final_score=score,
            recommended_quantity=1, entry_price=Decimal(100), final_mfe=mfe,
        )

    report = await build_paper_trading_report(db_session, period_start=PERIOD_START, period_end=PERIOD_END)

    assert report.score_outcome_correlation is not None
    assert report.score_outcome_correlation > Decimal("0.99")


async def test_trade_journal_win_rate_and_pnl_are_aggregated(db_session):
    await _seed_instrument_and_params(db_session)
    signal = await _seed_entry_signal(
        db_session, timestamp=PERIOD_START + timedelta(days=1), final_score=Decimal(90),
        recommended_quantity=2, entry_price=Decimal(100), final_mfe=Decimal(2),
    )
    losing_signal = await _seed_entry_signal(
        db_session, timestamp=PERIOD_START + timedelta(days=2), final_score=Decimal(85),
        recommended_quantity=1, entry_price=Decimal(50), final_mfe=Decimal(0),
    )

    db_session.add(
        Trade(
            signal_id=signal.signal_id, user_entry_time=PERIOD_START + timedelta(days=1),
            user_entry_price=Decimal(2), quantity=2, user_exit_time=PERIOD_START + timedelta(days=1, hours=1),
            user_exit_price=Decimal(3), realized_pnl=Decimal(200), exit_reason=ExitReason.TARGET_HIT,
        )
    )
    db_session.add(
        Trade(
            signal_id=losing_signal.signal_id, user_entry_time=PERIOD_START + timedelta(days=2),
            user_entry_price=Decimal(2), quantity=1, user_exit_time=PERIOD_START + timedelta(days=2, hours=1),
            user_exit_price=Decimal(1), realized_pnl=Decimal(-100), exit_reason=ExitReason.STOP_HIT,
        )
    )
    await db_session.commit()

    report = await build_paper_trading_report(db_session, period_start=PERIOD_START, period_end=PERIOD_END)

    assert report.logged_trades == 2
    assert report.trade_win_rate == 0.5
    assert report.total_realized_pnl == Decimal(100)


async def test_open_trade_without_a_realized_pnl_is_excluded_from_win_rate(db_session):
    await _seed_instrument_and_params(db_session)
    signal = await _seed_entry_signal(
        db_session, timestamp=PERIOD_START + timedelta(days=1), final_score=Decimal(90),
        recommended_quantity=1, entry_price=Decimal(100), final_mfe=Decimal(2),
    )
    db_session.add(
        Trade(
            signal_id=signal.signal_id, user_entry_time=PERIOD_START + timedelta(days=1),
            user_entry_price=Decimal(2), quantity=1,
        )
    )
    await db_session.commit()

    report = await build_paper_trading_report(db_session, period_start=PERIOD_START, period_end=PERIOD_END)

    assert report.logged_trades == 1
    assert report.trade_win_rate is None
    assert report.total_realized_pnl is None
