"""Broker-side adapters for QuantPlatformKit ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from application.account_payload_utils import (
    first_numeric_by_keywords,
    flatten_values,
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


_NEW_YORK_TZ = ZoneInfo("America/New_York")
_TOTAL_EQUITY_KEYWORD_GROUPS = (
    ("total", "value"),
    ("total", "equity"),
    ("account", "value"),
    ("account", "equity"),
    ("net", "liquid"),
    ("liquidation",),
    ("equity",),
)
_BUYING_POWER_KEYWORD_GROUPS = (
    ("buying", "power"),
    ("buying",),
    ("bp",),
)
_CASH_BALANCE_KEYWORD_GROUPS = (
    ("cash", "balance"),
    ("available", "cash"),
    ("cash", "available"),
    ("cash",),
)


def _extract_broker_order_id(payload) -> str | None:
    for key, value in flatten_values(payload).items():
        normalized = "".join(ch for ch in str(key or "").lower() if ch.isalnum())
        if "order" not in normalized:
            continue
        if not any(token in normalized for token in ("id", "number", "orderno")):
            continue
        text = str(value or "").strip()
        if text:
            return text
    return None


def _extract_broker_status_code(payload) -> int | None:
    for key, value in flatten_values(payload).items():
        leaf_key = str(key or "").rsplit(".", 1)[-1]
        normalized = "".join(ch for ch in leaf_key.lower() if ch.isalnum())
        if normalized != "statuscode":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _market_date(value: datetime) -> date:
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(_NEW_YORK_TZ).date()


def _first_numeric_by_keyword_groups(payload, keyword_groups: tuple[tuple[str, ...], ...]) -> float | None:
    for keywords in keyword_groups:
        value = first_numeric_by_keywords(payload, keywords)
        if value is not None:
            return value
    return None


def _positive_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    resolved = float(value)
    return resolved if resolved > 0.0 else None


def _resolve_buying_power(
    *,
    cash_balance: float | None,
    reported_buying_power: float | None,
    cash_only_execution: bool,
) -> float | None:
    if not cash_only_execution:
        return reported_buying_power if reported_buying_power is not None else cash_balance
    if cash_balance is None:
        return None
    if reported_buying_power is None:
        return cash_balance
    return max(0.0, min(float(cash_balance), float(reported_buying_power)))


def _resolve_total_equity(
    *,
    balances,
    cash_balance: float | None,
    buying_power: float | None,
    position_market_value: float,
    prefer_cash_plus_positions: bool = False,
    cash_only_execution: bool = True,
) -> tuple[float, str]:
    if cash_only_execution:
        if cash_balance is not None:
            combined_value = float(cash_balance) + max(0.0, float(position_market_value))
            if prefer_cash_plus_positions:
                return combined_value, "cash_plus_positions"
            if combined_value > 0.0:
                return combined_value, "cash_plus_positions"
    elif buying_power is not None:
        combined_value = float(buying_power) + max(0.0, float(position_market_value))
        if prefer_cash_plus_positions or combined_value > 0.0:
            return combined_value, "buying_power_plus_positions"

    balance_total = _first_numeric_by_keyword_groups(balances, _TOTAL_EQUITY_KEYWORD_GROUPS)
    if balance_total is not None:
        return float(balance_total), "balance_total"

    positive_position_value = _positive_or_none(position_market_value)
    if positive_position_value is not None:
        return positive_position_value, "positions"

    return 0.0, "unresolved"


@dataclass(frozen=True)
class FirstradeBrokerAdapters:
    client: FirstradeBrokerClient
    account: str
    strategy_symbols: tuple[str, ...] = ()
    account_hash: str | None = None
    clock: Callable[[], datetime] = _utcnow
    live_orders: bool = False
    live_order_ack: bool = False
    max_order_notional_usd: float | None = None
    cash_only_execution: bool = True

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
            try:
                quote = load_quote(normalized)
            except Exception:
                quote = None
            if quote is not None and quote.last_price > 0:
                quote_point = PricePoint(as_of=quote.as_of, close=quote.last_price)
                last_market_date = _market_date(points[-1].as_of)
                quote_market_date = _market_date(quote_point.as_of)
                if quote_market_date > last_market_date:
                    points.append(quote_point)
                elif quote_market_date == last_market_date:
                    points[-1] = quote_point
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
        cash_balance = _first_numeric_by_keyword_groups(balances, _CASH_BALANCE_KEYWORD_GROUPS)
        reported_buying_power = _first_numeric_by_keyword_groups(balances, _BUYING_POWER_KEYWORD_GROUPS)
        buying_power = _resolve_buying_power(
            cash_balance=cash_balance,
            reported_buying_power=reported_buying_power,
            cash_only_execution=self.cash_only_execution,
        )
        position_market_value = sum(position.market_value for position in positions)
        total_equity, total_equity_source = _resolve_total_equity(
            balances=balances,
            cash_balance=cash_balance,
            buying_power=buying_power,
            position_market_value=position_market_value,
            prefer_cash_plus_positions=bool(managed),
            cash_only_execution=self.cash_only_execution,
        )
        return PortfolioSnapshot(
            as_of=self.clock(),
            total_equity=float(total_equity),
            buying_power=float(buying_power or 0.0),
            cash_balance=cash_balance,
            positions=tuple(positions),
            metadata={
                "broker": "firstrade",
                "account_hash": self.account_hash or mask_account_id(self.account),
                "api_kind": "unofficial-reverse-engineered",
                "total_equity_source": total_equity_source,
                "cash_only_execution": self.cash_only_execution,
                "market_currency_cash": cash_balance,
                "available_funds": reported_buying_power,
            },
        )

    def build_portfolio_port(self) -> PortfolioPort:
        return CallablePortfolioPort(self.build_portfolio_snapshot)

    def build_execution_port(self) -> ExecutionPort:
        def submit(order_intent) -> ExecutionReport:
            metadata = dict(getattr(order_intent, "metadata", {}) or {})
            notional_usd = metadata.get("notional_usd")
            max_notional = metadata.get("max_notional_usd", self.max_order_notional_usd)
            if notional_usd is not None:
                request = StockOrderRequest(
                    account=self.account,
                    symbol=order_intent.symbol,
                    side=order_intent.side,
                    notional_usd=float(notional_usd),
                    price_type="market",
                    duration=str(order_intent.time_in_force or "day").lower(),
                    max_notional_usd=(
                        float(max_notional) if max_notional is not None else None
                    ),
                )
            else:
                request = StockOrderRequest(
                    account=self.account,
                    symbol=order_intent.symbol,
                    side=order_intent.side,
                    quantity=int(order_intent.quantity),
                    price_type=str(order_intent.order_type or "market").lower(),
                    duration=str(order_intent.time_in_force or "day").lower(),
                    limit_price=order_intent.limit_price,
                    max_notional_usd=(
                        float(max_notional) if max_notional is not None else None
                    ),
                )
            raw = self.client.place_stock_order(
                request,
                dry_run=not self.live_orders,
                explicit_live_ack=self.live_order_ack,
            )
            broker_order_id = _extract_broker_order_id(raw)
            broker_status_code = _extract_broker_status_code(raw)
            live_order_accepted = (
                broker_order_id is not None
                and (broker_status_code is None or 200 <= broker_status_code < 300)
            )
            return ExecutionReport(
                symbol=request.symbol,
                side=request.side,
                quantity=float(request.quantity or 0),
                status=(
                    "previewed"
                    if not self.live_orders
                    else ("submitted" if live_order_accepted else "rejected")
                ),
                broker_order_id=broker_order_id,
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
    max_order_notional_usd: float | None = None,
    cash_only_execution: bool = True,
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
        cash_only_execution=bool(cash_only_execution),
    )
