"""Test-only OptionQuote/OptionContractInfo factories."""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.data_providers.schemas import OptionContractInfo, OptionQuote
from app.models.enums import DataStatus, OptionType


def make_contract(
    *, contract_id: str = "TEST_1", symbol: str = "TEST", option_type: OptionType = OptionType.CALL,
    strike: float = 100, expiration: date = date(2026, 3, 20), dte: int = 21,
) -> OptionContractInfo:
    return OptionContractInfo(
        contract_id=contract_id, symbol=symbol, option_type=option_type,
        strike=Decimal(str(strike)), expiration=expiration, dte=dte,
    )


def make_quote(
    *, contract_id: str = "TEST_1", bid: float = 1.90, ask: float = 2.00, last: float = 1.95,
    volume: int = 500, open_interest: int = 1000, iv: float | None = 0.30, delta: float | None = 0.55,
    gamma: float | None = None, theta: float | None = None, vega: float | None = None,
) -> OptionQuote:
    return OptionQuote(
        contract_id=contract_id,
        timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        bid=Decimal(str(bid)), ask=Decimal(str(ask)), last=Decimal(str(last)),
        volume=volume, open_interest=open_interest,
        iv=Decimal(str(iv)) if iv is not None else None,
        delta=Decimal(str(delta)) if delta is not None else None,
        gamma=Decimal(str(gamma)) if gamma is not None else None,
        theta=Decimal(str(theta)) if theta is not None else None,
        vega=Decimal(str(vega)) if vega is not None else None,
        source="TEST", data_status=DataStatus.VALID,
    )
