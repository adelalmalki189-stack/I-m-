"""docs/API_SPEC_V0.1.md §5 — Trade Journal.

Purely manual, matching Methodology §13/§19 of the original spec: the
system never executes anything — a trade row only ever exists because the
user typed in what they actually did in IBKR themselves (docs/OPERATIONAL_SPEC_V0.1.md
§8: "النظام يُصدر توصيات قرار وليس تنفيذاً آلياً").
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.engines.risk_engine import DEFAULT_CONTRACT_MULTIPLIER
from app.models.enums import ExitReason
from app.models.signals import Signal
from app.models.trades import Trade

router = APIRouter(prefix="/trades", tags=["trades"])


def _error(status_code: int, error_code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error_code": error_code, "message": message})


class TradeCreateRequest(BaseModel):
    signal_id: str
    entry_time: datetime
    entry_price: Decimal
    quantity: int


class TradeExitRequest(BaseModel):
    exit_time: datetime
    exit_price: Decimal
    exit_reason: ExitReason | None = None


class TradeResponse(BaseModel):
    trade_id: str
    signal_id: str
    user_entry_time: datetime
    user_entry_price: Decimal
    quantity: int
    user_exit_time: datetime | None
    user_exit_price: Decimal | None
    realized_pnl: Decimal | None
    max_profit: Decimal | None
    max_loss: Decimal | None
    exit_reason: ExitReason | None


def _to_response(trade: Trade) -> TradeResponse:
    return TradeResponse(
        trade_id=trade.trade_id,
        signal_id=trade.signal_id,
        user_entry_time=trade.user_entry_time,
        user_entry_price=trade.user_entry_price,
        quantity=trade.quantity,
        user_exit_time=trade.user_exit_time,
        user_exit_price=trade.user_exit_price,
        realized_pnl=trade.realized_pnl,
        max_profit=trade.max_profit,
        max_loss=trade.max_loss,
        exit_reason=trade.exit_reason,
    )


@router.post("", response_model=TradeResponse, status_code=201)
async def create_trade(
    body: TradeCreateRequest, session: AsyncSession = Depends(get_db_session)
) -> TradeResponse:
    signal = await session.get(Signal, body.signal_id)
    if signal is None:
        raise _error(404, "SIGNAL_NOT_FOUND", f"No signal with id '{body.signal_id}'")

    trade = Trade(
        signal_id=body.signal_id,
        user_entry_time=body.entry_time,
        user_entry_price=body.entry_price,
        quantity=body.quantity,
    )
    session.add(trade)
    await session.commit()
    return _to_response(trade)


@router.get("", response_model=list[TradeResponse])
async def list_trades(
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[TradeResponse]:
    query = select(Trade)
    if from_ is not None:
        query = query.where(Trade.user_entry_time >= from_)
    if to is not None:
        query = query.where(Trade.user_entry_time <= to)
    query = query.order_by(Trade.user_entry_time.desc())

    result = await session.execute(query)
    return [_to_response(t) for t in result.scalars().all()]


@router.get("/{trade_id}", response_model=TradeResponse)
async def get_trade(trade_id: str, session: AsyncSession = Depends(get_db_session)) -> TradeResponse:
    trade = await session.get(Trade, trade_id)
    if trade is None:
        raise _error(404, "TRADE_NOT_FOUND", f"No trade with id '{trade_id}'")
    return _to_response(trade)


@router.put("/{trade_id}", response_model=TradeResponse)
async def exit_trade(
    trade_id: str, body: TradeExitRequest, session: AsyncSession = Depends(get_db_session)
) -> TradeResponse:
    trade = await session.get(Trade, trade_id)
    if trade is None:
        raise _error(404, "TRADE_NOT_FOUND", f"No trade with id '{trade_id}'")

    trade.user_exit_time = body.exit_time
    trade.user_exit_price = body.exit_price
    trade.exit_reason = body.exit_reason
    # Long-premium P&L only — this project's scope never sells options
    # (docs/DATA_SPEC_V0.1.md §15, no separate short/long field).
    trade.realized_pnl = (body.exit_price - trade.user_entry_price) * trade.quantity * DEFAULT_CONTRACT_MULTIPLIER

    await session.commit()
    return _to_response(trade)
