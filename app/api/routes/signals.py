"""docs/API_SPEC_V0.1.md §3 (Signals) — this phase implements only
GET /signals/{signal_id}/contracts, the Phase 3 completion criterion in
docs/IMPLEMENTATION_PLAN_V0.1.md ("Contract A/B/C" example, Methodology §15).
The rest of §3 (the full Signal object, "why this trade", snapshot,
outcome) lands with Phase 5's Dashboard work, once Signal/Timing/Risk are
all wired into a live scanner loop.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.engines.contract_engine import DEFAULT_MAX_DTE, DEFAULT_MIN_DTE, evaluate_and_rank_contracts
from app.models.enums import OptionType, SignalDirection
from app.models.iv_history import IVHistory
from app.models.signals import Signal

router = APIRouter(prefix="/signals", tags=["signals"])

MAX_CONTRACTS_RETURNED = 10
IV_HISTORY_LOOKBACK_DAYS = 252


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


def _error(status_code: int, error_code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error_code": error_code, "message": message})


@router.get("/{signal_id}/contracts", response_model=list[ContractCandidateResponse])
async def get_signal_contracts(
    signal_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> list[ContractCandidateResponse]:
    signal = await session.get(Signal, signal_id)
    if signal is None:
        raise _error(404, "SIGNAL_NOT_FOUND", f"No signal with id '{signal_id}'")

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
