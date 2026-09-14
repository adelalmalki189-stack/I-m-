"""docs/API_SPEC_V0.1.md §3 (Signals).

GET /signals/{id}/why is this project's realization of the original
spec's "WHY THIS TRADE?" panel (Methodology §19/§24): it reads the
`conditions`/`hard_gates` JSON frozen on the Signal row at decision time
(app/models/signals.py) rather than re-running the engines against
current data, which would silently answer a different question ("would
this still qualify now?") than the one the user is actually asking.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.engines.contract_engine import DEFAULT_MAX_DTE, DEFAULT_MIN_DTE, evaluate_and_rank_contracts
from app.models.enums import EventSeverity, OptionType, SignalDirection, SignalStatus
from app.models.iv_history import IVHistory
from app.models.outcomes import Outcome
from app.models.signal_snapshots import SignalSnapshot
from app.models.signals import Signal

router = APIRouter(prefix="/signals", tags=["signals"])

MAX_CONTRACTS_RETURNED = 10
IV_HISTORY_LOOKBACK_DAYS = 252
DEFAULT_LIST_LIMIT = 20


def _error(status_code: int, error_code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error_code": error_code, "message": message})


async def _get_signal_or_404(signal_id: str, session: AsyncSession) -> Signal:
    signal = await session.get(Signal, signal_id)
    if signal is None:
        raise _error(404, "SIGNAL_NOT_FOUND", f"No signal with id '{signal_id}'")
    return signal


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class SignalListItem(BaseModel):
    signal_id: str
    symbol: str
    direction: SignalDirection
    final_score: Decimal | None
    status: SignalStatus
    risk_reward: Decimal | None


class SignalDetailResponse(BaseModel):
    signal_id: str
    timestamp: datetime
    symbol: str
    direction: SignalDirection
    status: SignalStatus
    setup_score: Decimal | None
    contract_score: Decimal | None
    final_score: Decimal | None
    entry_zone_low: Decimal | None
    entry_zone_high: Decimal | None
    invalidation: Decimal | None
    target: Decimal | None
    risk_reward: Decimal | None
    event_risk: EventSeverity | None
    underlying_price: Decimal | None = None
    vwap: Decimal | None = None
    contract_id: str | None = None
    strike: Decimal | None = None
    expiration: date | None = None
    dte: int | None = None
    delta: Decimal | None = None
    iv: Decimal | None = None


class WhyThisTradeResponse(BaseModel):
    conditions: list[dict]
    hard_gates: dict[str, str]
    calc_version: str


class SnapshotResponse(BaseModel):
    underlying_price: Decimal
    vwap: Decimal
    ema20: Decimal | None
    ema50: Decimal | None
    rvol: Decimal | None
    spy_state: str | None
    qqq_state: str | None
    vix_value: Decimal | None
    sector_state: str | None
    contract_id: str | None
    strike: Decimal | None
    expiration: date | None
    dte: int | None
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    volume: int | None
    oi: int | None
    iv: Decimal | None
    delta: Decimal | None
    calc_version: str


class OutcomeItem(BaseModel):
    offset_minutes: int
    underlying_price_at_offset: Decimal
    option_price_at_offset: Decimal | None
    mfe: Decimal | None
    mae: Decimal | None


class ContractCandidateResponse(BaseModel):
    contract_id: str
    strike: Decimal
    expiration: date
    dte: int
    premium: Decimal
    delta: Decimal | None
    iv: Decimal | None
    iv_rank: Decimal | None
    open_interest: int
    volume: int
    spread_pct: Decimal
    score: Decimal


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[SignalListItem])
async def list_signals(
    status: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
    session: AsyncSession = Depends(get_db_session),
) -> list[SignalListItem]:
    """docs/API_SPEC_V0.1.md §3 — `status` is a comma-separated list, e.g.
    `?status=ENTRY,READY`, matching Methodology §23's Ranking view."""
    query = select(Signal)
    if status:
        try:
            statuses = [SignalStatus(s.strip()) for s in status.split(",") if s.strip()]
        except ValueError as exc:
            raise _error(400, "INVALID_STATUS", str(exc)) from exc
        query = query.where(Signal.status.in_(statuses))

    query = query.order_by(Signal.final_score.desc().nulls_last()).limit(limit)
    result = await session.execute(query)

    return [
        SignalListItem(
            signal_id=s.signal_id, symbol=s.symbol, direction=s.direction,
            final_score=s.final_score, status=s.status, risk_reward=s.risk_reward,
        )
        for s in result.scalars().all()
    ]


@router.get("/{signal_id}", response_model=SignalDetailResponse)
async def get_signal(signal_id: str, session: AsyncSession = Depends(get_db_session)) -> SignalDetailResponse:
    signal = await _get_signal_or_404(signal_id, session)
    snapshot = await session.get(SignalSnapshot, signal_id)

    return SignalDetailResponse(
        signal_id=signal.signal_id,
        timestamp=signal.timestamp,
        symbol=signal.symbol,
        direction=signal.direction,
        status=signal.status,
        setup_score=signal.setup_score,
        contract_score=signal.contract_score,
        final_score=signal.final_score,
        entry_zone_low=signal.entry_zone_low,
        entry_zone_high=signal.entry_zone_high,
        invalidation=signal.invalidation,
        target=signal.target,
        risk_reward=signal.risk_reward,
        event_risk=signal.event_risk,
        underlying_price=snapshot.underlying_price if snapshot else None,
        vwap=snapshot.vwap if snapshot else None,
        contract_id=snapshot.contract_id if snapshot else None,
        strike=snapshot.strike if snapshot else None,
        expiration=snapshot.expiration if snapshot else None,
        dte=snapshot.dte if snapshot else None,
        delta=snapshot.delta if snapshot else None,
        iv=snapshot.iv if snapshot else None,
    )


@router.get("/{signal_id}/why", response_model=WhyThisTradeResponse)
async def get_signal_why(
    signal_id: str, session: AsyncSession = Depends(get_db_session)
) -> WhyThisTradeResponse:
    signal = await _get_signal_or_404(signal_id, session)
    if signal.conditions is None or signal.hard_gates is None:
        raise _error(
            404, "WHY_NOT_AVAILABLE", "No condition/hard-gate breakdown was recorded for this signal"
        )
    return WhyThisTradeResponse(
        conditions=signal.conditions, hard_gates=signal.hard_gates, calc_version=signal.calc_version
    )


@router.get("/{signal_id}/snapshot", response_model=SnapshotResponse)
async def get_signal_snapshot(
    signal_id: str, session: AsyncSession = Depends(get_db_session)
) -> SnapshotResponse:
    await _get_signal_or_404(signal_id, session)
    snapshot = await session.get(SignalSnapshot, signal_id)
    if snapshot is None:
        raise _error(404, "SNAPSHOT_NOT_FOUND", f"No snapshot recorded for signal '{signal_id}'")
    return SnapshotResponse(
        underlying_price=snapshot.underlying_price, vwap=snapshot.vwap, ema20=snapshot.ema20,
        ema50=snapshot.ema50, rvol=snapshot.rvol, spy_state=snapshot.spy_state,
        qqq_state=snapshot.qqq_state, vix_value=snapshot.vix_value, sector_state=snapshot.sector_state,
        contract_id=snapshot.contract_id, strike=snapshot.strike, expiration=snapshot.expiration,
        dte=snapshot.dte, bid=snapshot.bid, ask=snapshot.ask, last=snapshot.last,
        volume=snapshot.volume, oi=snapshot.oi, iv=snapshot.iv, delta=snapshot.delta,
        calc_version=snapshot.calc_version,
    )


@router.get("/{signal_id}/outcome", response_model=list[OutcomeItem])
async def get_signal_outcome(
    signal_id: str, session: AsyncSession = Depends(get_db_session)
) -> list[OutcomeItem]:
    await _get_signal_or_404(signal_id, session)
    result = await session.execute(
        select(Outcome).where(Outcome.signal_id == signal_id).order_by(Outcome.offset_minutes)
    )
    return [
        OutcomeItem(
            offset_minutes=o.offset_minutes,
            underlying_price_at_offset=o.underlying_price_at_offset,
            option_price_at_offset=o.option_price_at_offset,
            mfe=o.mfe, mae=o.mae,
        )
        for o in result.scalars().all()
    ]


@router.get("/{signal_id}/contracts", response_model=list[ContractCandidateResponse])
async def get_signal_contracts(
    signal_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> list[ContractCandidateResponse]:
    signal = await _get_signal_or_404(signal_id, session)

    provider = getattr(request.app.state, "data_provider", None)
    if provider is None:
        raise _error(503, "DATA_PROVIDER_UNAVAILABLE", "No DataProvider is attached to this instance")

    option_type = OptionType.CALL if signal.direction == SignalDirection.CALL else OptionType.PUT

    chain = await provider.get_option_chain(signal.symbol)
    dte_filtered = [
        c for c in chain
        if c.option_type == option_type and DEFAULT_MIN_DTE <= c.dte <= DEFAULT_MAX_DTE
    ]

    candidates = []
    for contract in dte_filtered:
        quote = await provider.get_option_quote(contract.contract_id)
        candidates.append((contract, quote))

    iv_history_result = await session.execute(
        select(IVHistory.atm_iv)
        .where(IVHistory.symbol == signal.symbol)
        .order_by(IVHistory.date.desc())
        .limit(IV_HISTORY_LOOKBACK_DAYS)
    )
    # IVHistory.atm_iv is typed Mapped[float] (see docs/DATA_SPEC_V0.1.md
    # §18) but the driver returns Decimal at runtime for a Numeric column —
    # normalize explicitly rather than depending on that runtime detail.
    historical_ivs = [Decimal(str(v)) for v in iv_history_result.scalars().all()]

    ranked = evaluate_and_rank_contracts(candidates, historical_ivs=historical_ivs)

    return [
        ContractCandidateResponse(
            contract_id=e.contract.contract_id,
            strike=e.contract.strike,
            expiration=e.contract.expiration,
            dte=e.contract.dte,
            premium=(e.quote.bid + e.quote.ask) / 2,
            delta=e.quote.delta,
            iv=e.quote.iv,
            iv_rank=e.iv_rank,
            open_interest=e.quote.open_interest,
            volume=e.quote.volume,
            spread_pct=e.quote.spread_pct,
            score=e.score,
        )
        for e in ranked[:MAX_CONTRACTS_RETURNED]
    ]
