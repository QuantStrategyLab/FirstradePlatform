"""Broker-side adapters for QuantPlatformKit ports."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

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


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _flatten_values(payload: Any, prefix: str = "") -> dict[str, Any]:
    values: dict[str, Any] = {}
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            child_key = f"{prefix}.{key}" if prefix else str(key)
            values.update(_flatten_values(value, child_key))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            values.update(_flatten_values(value, f"{prefix}.{index}"))
    else:
        values[prefix] = payload
    return values


def _first_numeric_by_keywords(payload: Any, keywords: tuple[str, ...]) -> float | None:
    flat = _flatten_values(payload)
    for key, value in flat.items():
        key_lower = key.lower()
        if all(keyword in key_lower for keyword in keywords):
            number = _float_or_none(value)
            if number is not None:
                return number
    return None


def _iter_position_rows(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        for key in ("items", "positions", "data", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, Mapping)]
        if "symbol" in payload:
            return [payload]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, Mapping)]
    return []


def _get_first(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    lower_map = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        lowered = key.lower()
        if lowered in lower_map:
            return lower_map[lowered]
    return None


@dataclass(frozen=True)
class FirstradeBrokerAdapters:
    client: FirstradeBrokerClient
    account: str
    strategy_symbols: tuple[str, ...] = ()
    account_hash: str | None = None
    clock: Callable[[], datetime] = _utcnow
    live_orders: bool = False

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
            price = _float_or_none(payload.get("last"))
            if price is None:
                raise ValueError(f"Firstrade quote did not include a numeric last price for {normalized}.")
            snapshot = QuoteSnapshot(
                symbol=normalized,
                as_of=self.clock(),
                last_price=price,
                bid_price=_float_or_none(payload.get("bid")),
                ask_price=_float_or_none(payload.get("ask")),
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
                        volume=_float_or_none(candle[5] if len(candle) > 5 else None),
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

    def build_portfolio_snapshot(self) -> PortfolioSnapshot:
        balances = self.client.get_balances(self.account)
        positions_payload = self.client.get_positions(self.account)
        rows = _iter_position_rows(positions_payload)
        positions = []
        managed = set(self.strategy_symbols)
        for row in rows:
            raw_symbol = _get_first(row, "symbol", "ticker", "security_symbol")
            if not raw_symbol:
                continue
            symbol = self.normalize_symbol(raw_symbol)
            if managed and symbol not in managed:
                continue
            quantity = _float_or_none(_get_first(row, "quantity", "shares", "qty"))
            if quantity is None:
                continue
            positions.append(
                Position(
                    symbol=symbol,
                    quantity=quantity,
                    market_value=_float_or_none(
                        _get_first(row, "market_value", "marketValue", "value", "current_value")
                    )
                    or 0.0,
                    average_cost=_float_or_none(
                        _get_first(row, "average_cost", "avg_cost", "cost_basis", "averagePrice")
                    ),
                    currency="USD",
                    account_id=mask_account_id(self.account),
                )
            )
        total_equity = (
            _first_numeric_by_keywords(balances, ("total", "value"))
            or _first_numeric_by_keywords(balances, ("equity",))
            or sum(position.market_value for position in positions)
        )
        return PortfolioSnapshot(
            as_of=self.clock(),
            total_equity=float(total_equity or 0.0),
            buying_power=_first_numeric_by_keywords(balances, ("buying",))
            or _first_numeric_by_keywords(balances, ("bp",)),
            cash_balance=_first_numeric_by_keywords(balances, ("cash",)),
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
            )
            raw = self.client.place_stock_order(request, dry_run=not self.live_orders)
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
) -> FirstradeBrokerAdapters:
    return FirstradeBrokerAdapters(
        client=client,
        account=account,
        strategy_symbols=strategy_symbols,
        account_hash=account_hash,
        clock=clock,
        live_orders=live_orders,
    )
