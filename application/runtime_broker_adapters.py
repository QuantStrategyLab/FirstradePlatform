"""Broker-side adapters for QuantPlatformKit ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from application.account_payload_utils import (
    first_numeric_by_keywords,
    float_or_none,
    get_first,
    iter_position_rows,
)
from application.firstrade_client import (
    FirstradeBrokerClient,
    StockOrderRequest,
    mask_account_id,
)
from quant_platform_kit.common.models import (
    ExecutionReport,
    PortfolioSnapshot,
    Position,
    PricePoint,
    PriceSeries,
    QuoteSnapshot,
)
from quant_platform_kit.common.port_adapters import (
    CallableExecutionPort,
    CallableMarketDataPort,
    CallablePortfolioPort,
)
from quant_platform_kit.common.ports import ExecutionPort, MarketDataPort, PortfolioPort


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class FirstradeBrokerAdapters:
    client: FirstradeBrokerClient
    account: str
    strategy_symbols: tuple[str, ...] = ()
    account_hash: str | None = None
    clock: Callable[[], datetime] = _utcnow
    live_orders: bool = False
    live_order_ack: bool = False
    max_order_notional_usd: float = 25.0

    def normalize_symbol(self, symbol: str) -> str:
        value = str(symbol or "").strip().upper()
        if not value:
            raise ValueError("Symbol must be non-empty.")
        return value

    def build_market_data_port(self) -> MarketDataPort:
        quote_cache: dict[str, QuoteSnapshot] = {}
        series_cache: dict[str, PriceSeries] = {}

        def load_quote(symbol: str) -> QuoteSnapshot:
            normalized = self.normalize_symbol(symbol)
            cached = quote_cache.get(normalized)
            if cached is not None:
                return cached
            payload = self.client.get_quote(self.account, normalized)
            price = float_or_none(payload.get("last"))
            if price is None:
                raise ValueError(f"Firstrade quote did not include a numeric last price for {normalized}.")
            snapshot = QuoteSnapshot(
                symbol=normalized,
                as_of=self.clock(),
                last_price=price,
                bid_price=float_or_none(payload.get("bid")),
                ask_price=float_or_none(payload.get("ask")),
                currency="USD",
            )
            quote_cache[normalized] = snapshot
            return snapshot

        def load_price_series(symbol: str) -> PriceSeries:
            normalized = self.normalize_symbol(symbol)
            cached = series_cache.get(normalized)
            if cached is not None:
                return cached
            candles = self.client.get_ohlc(normalized, "1y")
            points = []
            for candle in candles:
                if len(candle) < 5:
                    continue
                timestamp_ms = int(candle[0])
                close = float(candle[4])
                points.append(
                    PricePoint(
                        as_of=datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc),
                        close=close,
                        volume=float_or_none(candle[5] if len(candle) > 5 else None),
                    )
                )
            if not points:
                raise ValueError(f"Firstrade OHLC did not return price history for {normalized}.")
            series = PriceSeries(symbol=normalized, currency="USD", points=tuple(points))
            series_cache[normalized] = series
            return series

        return CallableMarketDataPort(
            quote_loader=load_quote,
            price_series_loader=load_price_series,
        )

    def build_market_history_loader(self, market_data_port: MarketDataPort):
        def load_market_history(_broker_client, symbol, *_args, **_kwargs):
            series = market_data_port.get_price_series(str(symbol).strip().upper())
            if not series.points:
                return pd.Series(dtype=float)
            index = pd.DatetimeIndex([pd.Timestamp(point.as_of) for point in series.points])
            closes = [float(point.close) for point in series.points]
            return pd.Series(closes, index=index, dtype=float)

        return load_market_history

    def build_price_history(self, market_data_port: MarketDataPort, symbol: str):
        series = market_data_port.get_price_series(symbol)
        return [
            {
                "close": float(point.close),
                "high": float(point.close),
                "low": float(point.close),
            }
            for point in series.points
        ]

    def build_portfolio_snapshot(self) -> PortfolioSnapshot:
        balances = self.client.get_balances(self.account)
        positions_payload = self.client.get_positions(self.account)
        rows = iter_position_rows(positions_payload)
        positions = []
        managed = set(self.strategy_symbols)
        for row in rows:
            raw_symbol = get_first(row, "symbol", "ticker", "security_symbol")
            if not raw_symbol:
                continue
            symbol = self.normalize_symbol(raw_symbol)
            if managed and symbol not in managed:
                continue
            quantity = float_or_none(get_first(row, "quantity", "shares", "qty"))
            if quantity is None:
                continue
            positions.append(
                Position(
                    symbol=symbol,
                    quantity=quantity,
                    market_value=float_or_none(
                        get_first(row, "market_value", "marketValue", "value", "current_value")
                    )
                    or 0.0,
                    average_cost=float_or_none(
                        get_first(row, "average_cost", "avg_cost", "cost_basis", "averagePrice")
                    ),
                    currency="USD",
                    account_id=mask_account_id(self.account),
                )
            )
        total_equity = (
            first_numeric_by_keywords(balances, ("total", "value"))
            or first_numeric_by_keywords(balances, ("equity",))
            or sum(position.market_value for position in positions)
        )
        return PortfolioSnapshot(
            as_of=self.clock(),
            total_equity=float(total_equity or 0.0),
            buying_power=first_numeric_by_keywords(balances, ("buying",))
            or first_numeric_by_keywords(balances, ("bp",)),
            cash_balance=first_numeric_by_keywords(balances, ("cash",)),
            positions=tuple(positions),
            metadata={
                "broker": "firstrade",
                "account_hash": self.account_hash or mask_account_id(self.account),
                "api_kind": "unofficial-reverse-engineered",
            },
        )

    def build_portfolio_port(self) -> PortfolioPort:
        return CallablePortfolioPort(self.build_portfolio_snapshot)

    def build_execution_port(self) -> ExecutionPort:
        def submit(order_intent) -> ExecutionReport:
            request = StockOrderRequest(
                account=self.account,
                symbol=order_intent.symbol,
                side=order_intent.side,
                quantity=int(order_intent.quantity),
                price_type=str(order_intent.order_type or "market").lower(),
                duration=str(order_intent.time_in_force or "day").lower(),
                limit_price=order_intent.limit_price,
                max_notional_usd=float(
                    (getattr(order_intent, "metadata", {}) or {}).get(
                        "max_notional_usd",
                        self.max_order_notional_usd,
                    )
                ),
            )
            raw = self.client.place_stock_order(
                request,
                dry_run=not self.live_orders,
                explicit_live_ack=self.live_order_ack,
            )
            return ExecutionReport(
                symbol=request.symbol,
                side=request.side,
                quantity=float(request.quantity or 0),
                status="previewed" if not self.live_orders else "submitted",
                raw_payload=raw,
            )

        return CallableExecutionPort(submit)


def build_runtime_broker_adapters(
    *,
    client: FirstradeBrokerClient,
    account: str,
    strategy_symbols: tuple[str, ...] = (),
    account_hash: str | None = None,
    clock: Callable[[], datetime] = _utcnow,
    live_orders: bool = False,
    live_order_ack: bool = False,
    max_order_notional_usd: float = 25.0,
) -> FirstradeBrokerAdapters:
    return FirstradeBrokerAdapters(
        client=client,
        account=account,
        strategy_symbols=strategy_symbols,
        account_hash=account_hash,
        clock=clock,
        live_orders=live_orders,
        live_order_ack=live_order_ack,
        max_order_notional_usd=max_order_notional_usd,
    )
