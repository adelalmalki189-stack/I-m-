"""IBKRDataProvider — production DataProvider backed by TWS/IB Gateway.

Built against the Paper Trading endpoint per
docs/IMPLEMENTATION_PLAN_V0.1.md Phase 0 (settings.ibkr_port defaults to
7497, TWS paper). Uses `ib_insync` rather than the raw `ibapi` client for a
much smaller amount of connection/reconnection boilerplate.

Historical + live 5m candles both go through `reqHistoricalData`, the
second call with `keepUpToDate=True` so IBKR itself finalizes each bar —
this repo never assembles candles from raw ticks, which is what makes the
`is_complete` guarantee in the DataProvider contract trustworthy.
"""
from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.core.config import settings
from app.data_providers.base import CandleCallback, DataProvider, OptionQuoteCallback
from app.data_providers.registry import register_production_provider
from app.data_providers.schemas import (
    Candle,
    MarketContextData,
    OptionContractInfo,
    OptionQuote,
    UnderlyingQuote,
)
from app.models.enums import DataStatus, OptionType

SOURCE_NAME = "IBKR"


def _safe_decimal(value: float | None) -> Decimal | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return Decimal(str(value))


def _age_seconds(ts: datetime | None) -> float:
    if ts is None:
        return math.inf
    now = datetime.now(UTC)
    return (now - ts.astimezone(UTC)).total_seconds()


class IBKRDataProvider(DataProvider):
    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        client_id: int | None = None,
    ) -> None:
        from ib_insync import IB  # imported lazily so unit tests never require the package

        self._ib = IB()
        self._host = host or settings.ibkr_host
        self._port = port or settings.ibkr_port
        self._client_id = client_id or settings.ibkr_client_id
        self._live_bars: dict[str, Any] = {}  # symbol -> ib_insync BarDataList
        self._candle_callbacks: dict[str, list[CandleCallback]] = {}
        self._option_quote_callbacks: dict[str, list[OptionQuoteCallback]] = {}

    async def connect(self) -> None:
        """Idempotent, with exponential backoff per
        docs/OPERATIONAL_SPEC_V0.1.md §3 (2s, 4s, 8s, 16s, 30s cap)."""
        if self._ib.isConnected():
            return

        delays = [2, 4, 8, 16, 30]
        last_error: Exception | None = None
        for delay in [0, *delays]:
            if delay:
                await asyncio.sleep(delay)
            try:
                await self._ib.connectAsync(self._host, self._port, clientId=self._client_id)
                return
            except Exception as exc:  # noqa: BLE001 — genuinely any connect failure retries
                last_error = exc
        raise ConnectionError(f"Failed to connect to IBKR after retries: {last_error}")

    async def disconnect(self) -> None:
        self._ib.disconnect()

    @property
    def is_connected(self) -> bool:
        return self._ib.isConnected()

    def _stock_contract(self, symbol: str) -> Any:
        from ib_insync import Stock

        return Stock(symbol, "SMART", "USD")

    def _bar_to_candle(self, symbol: str, bar: Any, *, is_complete: bool) -> Candle:
        ts = bar.date if isinstance(bar.date, datetime) else datetime.combine(
            bar.date, datetime.min.time(), tzinfo=UTC
        )
        return Candle(
            symbol=symbol,
            timestamp=ts,
            open=Decimal(str(bar.open)),
            high=Decimal(str(bar.high)),
            low=Decimal(str(bar.low)),
            close=Decimal(str(bar.close)),
            volume=int(bar.volume),
            vwap=Decimal(str(bar.average)) if getattr(bar, "average", None) else Decimal(str(bar.close)),
            is_complete=is_complete,
            source=SOURCE_NAME,
            data_status=DataStatus.VALID,
        )

    async def get_historical_candles_5m(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[Candle]:
        contract = self._stock_contract(symbol)
        duration_days = max(1, (end - start).days + 1)
        bars = await self._ib.reqHistoricalDataAsync(
            contract,
            endDateTime=end,
            durationStr=f"{duration_days} D",
            barSizeSetting="5 mins",
            whatToShow="TRADES",
            useRTH=True,
            keepUpToDate=False,
        )
        return [self._bar_to_candle(symbol, b, is_complete=True) for b in bars]

    async def subscribe_candles_5m(self, symbol: str, callback: CandleCallback) -> None:
        self._candle_callbacks.setdefault(symbol, []).append(callback)

        if symbol in self._live_bars:
            return  # already subscribed; callback list above is enough

        contract = self._stock_contract(symbol)
        bars = self._ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr="1 D",
            barSizeSetting="5 mins",
            whatToShow="TRADES",
            useRTH=True,
            keepUpToDate=True,
        )
        self._live_bars[symbol] = bars

        def _on_update(updated_bars: Any, has_new_bar: bool) -> None:
            # ib_insync appends a new (forming) bar once the previous one
            # closes — so the second-to-last entry is the one that just
            # became complete. Never fire on the still-forming last bar:
            # that would violate the no-look-ahead rule in
            # docs/CALCULATION_SPEC_V0.1.md.
            if not has_new_bar or len(updated_bars) < 2:
                return
            closed_candle = self._bar_to_candle(symbol, updated_bars[-2], is_complete=True)
            for cb in self._candle_callbacks.get(symbol, []):
                asyncio.ensure_future(cb(closed_candle))

        bars.updateEvent += _on_update

    async def get_underlying_quote(self, symbol: str) -> UnderlyingQuote:
        contract = self._stock_contract(symbol)
        [ticker] = await self._ib.reqTickersAsync(contract)

        bid, ask, last = _safe_decimal(ticker.bid), _safe_decimal(ticker.ask), _safe_decimal(ticker.last)
        status = DataStatus.VALID
        if bid is None or ask is None:
            status = DataStatus.INVALID
        elif _age_seconds(ticker.time) > settings.underlying_quote_stale_seconds:
            status = DataStatus.STALE

        return UnderlyingQuote(
            symbol=symbol,
            timestamp=ticker.time or datetime.now(UTC),
            bid=bid or Decimal(0),
            ask=ask or Decimal(0),
            last=last or Decimal(0),
            volume=int(ticker.volume) if ticker.volume and not math.isnan(ticker.volume) else 0,
            source=SOURCE_NAME,
            data_status=status,
        )

    async def get_option_chain(self, symbol: str) -> list[OptionContractInfo]:
        underlying = self._stock_contract(symbol)
        [qualified] = await self._ib.qualifyContractsAsync(underlying)
        chains = await self._ib.reqSecDefOptParamsAsync(
            qualified.symbol, "", qualified.secType, qualified.conId
        )

        contracts: list[OptionContractInfo] = []
        today = datetime.now(UTC).date()
        for chain in chains:
            for expiration_str in sorted(chain.expirations):
                expiration = datetime.strptime(expiration_str, "%Y%m%d").date()
                dte = (expiration - today).days
                for strike in chain.strikes:
                    for right, option_type in (("C", OptionType.CALL), ("P", OptionType.PUT)):
                        contracts.append(
                            OptionContractInfo(
                                contract_id=f"{symbol}_{expiration_str}_{right}_{strike}",
                                symbol=symbol,
                                option_type=option_type,
                                strike=Decimal(str(strike)),
                                expiration=expiration,
                                dte=dte,
                            )
                        )
        return contracts

    async def get_option_quote(self, contract_id: str) -> OptionQuote:
        symbol, expiration_str, right, strike_str = contract_id.split("_")
        from ib_insync import Option

        contract = Option(symbol, expiration_str, float(strike_str), right, "SMART")
        [ticker] = await self._ib.reqTickersAsync(contract)
        greeks = ticker.modelGreeks

        bid, ask, last = _safe_decimal(ticker.bid), _safe_decimal(ticker.ask), _safe_decimal(ticker.last)
        status = DataStatus.VALID
        if bid is None or ask is None:
            status = DataStatus.INVALID
        elif _age_seconds(ticker.time) > settings.option_quote_stale_seconds:
            status = DataStatus.STALE

        return OptionQuote(
            contract_id=contract_id,
            timestamp=ticker.time or datetime.now(UTC),
            bid=bid or Decimal(0),
            ask=ask or Decimal(0),
            last=last or Decimal(0),
            volume=int(ticker.volume) if ticker.volume and not math.isnan(ticker.volume) else 0,
            open_interest=int(ticker.callOpenInterest or ticker.putOpenInterest or 0),
            iv=_safe_decimal(greeks.impliedVol) if greeks else None,
            delta=_safe_decimal(greeks.delta) if greeks else None,
            gamma=_safe_decimal(greeks.gamma) if greeks else None,
            theta=_safe_decimal(greeks.theta) if greeks else None,
            vega=_safe_decimal(greeks.vega) if greeks else None,
            source=SOURCE_NAME,
            data_status=status,
        )

    async def subscribe_option_quote(self, contract_id: str, callback: OptionQuoteCallback) -> None:
        self._option_quote_callbacks.setdefault(contract_id, []).append(callback)
        # Polling fallback for Phase 0 — a push-based ib_insync ticker
        # subscription is a Phase 3 (Options Chain + Contract Engine) concern.

    async def get_market_context(self) -> MarketContextData:
        from ib_insync import Index, Stock

        spy = Stock("SPY", "SMART", "USD")
        qqq = Stock("QQQ", "SMART", "USD")
        vix = Index("VIX", "CBOE")

        tickers = await self._ib.reqTickersAsync(spy, qqq, vix)
        spy_t, qqq_t, vix_t = tickers

        prices = [_safe_decimal(t.last) or _safe_decimal(t.close) for t in (spy_t, qqq_t, vix_t)]
        status = DataStatus.VALID if all(p is not None for p in prices) else DataStatus.INVALID

        return MarketContextData(
            timestamp=datetime.now(UTC),
            spy_price=prices[0] or Decimal(0),
            qqq_price=prices[1] or Decimal(0),
            vix_value=prices[2] or Decimal(0),
            source=SOURCE_NAME,
            data_status=status,
        )


register_production_provider("IBKR", IBKRDataProvider)
