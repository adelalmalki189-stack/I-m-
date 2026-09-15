"""Paper Trading Report — Phase 8's diagnostic tool for an actual IBKR
Paper run (docs/PAPER_TRADING_PLAN_V0.1.md).

Mirrors app/scripts/run_rule_sanity_check.py's report shape (signal
frequency, score distribution, a score-vs-outcome relationship) but reads
real persisted rows — Signal / Outcome / Trade — instead of a synthetic
Replay, so a live Paper run can be compared directly against the Phase 7
baseline already recorded in docs/RULE_SANITY_CHECK_RESULTS_V0.1.md, per
docs/BACKTEST_PLAN_V0.1.md §6's own "first reference point" framing.

It also reports actual realized P&L from the manually-logged Trade
Journal (app/models/trades.py) — the one number neither the Rule Sanity
Check nor this report's own MFE/MAE section can provide, since both of
those are model-side outcome estimates, not confirmation that a human
actually took and closed the trade.

Usage:
    python -m app.scripts.run_paper_trading_report --days 14
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.engines.outcome_tracker import OUTCOME_OFFSETS_MINUTES
from app.models.enums import SignalStatus
from app.models.outcomes import Outcome
from app.models.risk import RiskPlan
from app.models.signal_snapshots import SignalSnapshot
from app.models.signals import Signal
from app.models.trades import Trade

DEFAULT_LOOKBACK_DAYS = 14
TRADING_DAYS_PER_WEEK = 5
FINAL_OUTCOME_OFFSET_MINUTES = max(OUTCOME_OFFSETS_MINUTES)


@dataclass(frozen=True, slots=True)
class PaperTradingReport:
    period_start: datetime
    period_end: datetime
    total_signals: int
    entry_signals: int
    invalidated_signals: int
    signals_per_week: float
    mean_final_score: Decimal | None
    final_gate_pass_rate: float
    mean_final_outcome_pct: Decimal | None
    score_outcome_correlation: float | None
    logged_trades: int
    trade_win_rate: float | None
    total_realized_pnl: Decimal | None


def _correlation(scores: list[float], outcomes: list[float]) -> float | None:
    if len(scores) < 2 or len(set(scores)) < 2 or len(set(outcomes)) < 2:
        return None
    return statistics.correlation(scores, outcomes)


async def _final_outcome_pct(session: AsyncSession, signal: Signal) -> Decimal | None:
    """The furthest checkpoint's MFE, normalized to a % of the entry price
    so signals on differently-priced underlyings are comparable — the same
    reasoning app/replay/harness.py's `_forward_return` proxy uses, just
    reading a real recorded Outcome instead of a synthetic forward candle."""
    snapshot = await session.get(SignalSnapshot, signal.signal_id)
    if snapshot is None or snapshot.underlying_price <= 0:
        return None

    result = await session.execute(
        select(Outcome)
        .where(Outcome.signal_id == signal.signal_id)
        .order_by(Outcome.offset_minutes.desc())
    )
    outcome = result.scalars().first()
    if outcome is None or outcome.mfe is None:
        return None
    return outcome.mfe / snapshot.underlying_price


async def build_paper_trading_report(
    session: AsyncSession, *, period_start: datetime, period_end: datetime
) -> PaperTradingReport:
    signals_result = await session.execute(
        select(Signal).where(Signal.timestamp >= period_start, Signal.timestamp <= period_end)
    )
    signals = list(signals_result.scalars().all())

    entry_signals = [
        s for s in signals
        if s.status in (SignalStatus.ENTRY, SignalStatus.INVALIDATED) or s.final_score is not None
    ]
    invalidated_signals = [s for s in signals if s.status == SignalStatus.INVALIDATED]

    scored_signals: list[tuple[Signal, Decimal]] = [
        (s, s.final_score) for s in signals if s.final_score is not None
    ]
    mean_final_score = (
        Decimal(str(statistics.mean(float(score) for _, score in scored_signals))) if scored_signals else None
    )

    pass_count = 0
    scored_pairs: list[tuple[float, float]] = []
    for signal, score in scored_signals:
        plan = await session.get(RiskPlan, signal.signal_id)
        if plan is not None and plan.recommended_quantity >= 1:
            pass_count += 1

        outcome_pct = await _final_outcome_pct(session, signal)
        if outcome_pct is not None:
            scored_pairs.append((float(score), float(outcome_pct)))

    final_gate_pass_rate = pass_count / len(scored_signals) if scored_signals else 0.0
    mean_final_outcome_pct = (
        Decimal(str(statistics.mean(o for _, o in scored_pairs))) if scored_pairs else None
    )
    correlation = _correlation([s for s, _ in scored_pairs], [o for _, o in scored_pairs])

    period_days = max((period_end - period_start).days, 1)
    signals_per_week = len(entry_signals) / (period_days / TRADING_DAYS_PER_WEEK)

    trades_result = await session.execute(
        select(Trade).where(Trade.user_entry_time >= period_start, Trade.user_entry_time <= period_end)
    )
    trades = list(trades_result.scalars().all())
    closed_pnls: list[Decimal] = [t.realized_pnl for t in trades if t.realized_pnl is not None]
    trade_win_rate = (sum(1 for p in closed_pnls if p > 0) / len(closed_pnls)) if closed_pnls else None
    total_realized_pnl = sum(closed_pnls, start=Decimal(0)) if closed_pnls else None

    return PaperTradingReport(
        period_start=period_start,
        period_end=period_end,
        total_signals=len(signals),
        entry_signals=len(entry_signals),
        invalidated_signals=len(invalidated_signals),
        signals_per_week=signals_per_week,
        mean_final_score=mean_final_score,
        final_gate_pass_rate=final_gate_pass_rate,
        mean_final_outcome_pct=mean_final_outcome_pct,
        score_outcome_correlation=correlation,
        logged_trades=len(trades),
        trade_win_rate=trade_win_rate,
        total_realized_pnl=total_realized_pnl,
    )


def format_report(report: PaperTradingReport) -> str:
    lines = [
        "Paper Trading Report (docs/PAPER_TRADING_PLAN_V0.1.md) "
        f"-- {report.period_start.date()} to {report.period_end.date()}",
        "",
        f"Total signal rows:        {report.total_signals}",
        f"Signals reaching ENTRY/INVALIDATED (or scored): {report.entry_signals}",
        f"  of which INVALIDATED:  {report.invalidated_signals}",
        f"Signals per week:         {report.signals_per_week:.2f}",
        f"Mean final score:         "
        f"{round(report.mean_final_score, 1) if report.mean_final_score is not None else '-'}",
        f"Final Hard Gate pass rate: {report.final_gate_pass_rate * 100:.1f}%",
        f"Mean final-checkpoint MFE (% of entry price): "
        f"{round(report.mean_final_outcome_pct * 100, 2) if report.mean_final_outcome_pct is not None else '-'}",
        f"Score-vs-outcome correlation: "
        f"{f'{report.score_outcome_correlation:.2f}' if report.score_outcome_correlation is not None else '-'}",
        "",
        f"Logged trades (Trade Journal): {report.logged_trades}",
        f"  win rate: {f'{report.trade_win_rate * 100:.1f}%' if report.trade_win_rate is not None else '-'}",
        f"  total realized P&L: {report.total_realized_pnl if report.total_realized_pnl is not None else '-'}",
        "",
        "Compare against docs/RULE_SANITY_CHECK_RESULTS_V0.1.md (the Phase 7 synthetic-data "
        "baseline) -- signal frequency and score distribution should be in a broadly similar "
        "range; a large divergence is worth investigating before trusting this period's numbers.",
    ]
    return "\n".join(lines)


async def main(days: int) -> None:
    period_end = datetime.now(UTC)
    period_start = period_end - timedelta(days=days)
    async with get_session() as session:
        report = await build_paper_trading_report(session, period_start=period_start, period_end=period_end)
    print(format_report(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    args = parser.parse_args()
    asyncio.run(main(args.days))
