"""Dry-run-first value-target execution planning for FirstradePlatform."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quant_platform_kit.common.models import OrderIntent
from quant_platform_kit.common.ports import ExecutionPort, MarketDataPort


@dataclass(frozen=True)
class ExecutionCycleResult:
    submitted_orders: tuple[dict[str, Any], ...]
    skipped_orders: tuple[dict[str, Any], ...]
    action_done: bool


def _floor_quantity(quantity: float) -> int:
    return max(0, int(float(quantity or 0.0)))


def _quote_price(market_data_port: MarketDataPort, symbol: str) -> float | None:
    try:
        price = float(market_data_port.get_quote(symbol).last_price)
    except Exception:
        return None
    return price if price > 0 else None


def _submit_order(
    execution_port: ExecutionPort,
    *,
    symbol: str,
    side: str,
    quantity: int,
    limit_price: float,
    max_notional_usd: float,
) -> dict[str, Any]:
    report = execution_port.submit_order(
        OrderIntent(
            symbol=symbol,
            side=side,
            quantity=float(quantity),
            order_type="limit",
            limit_price=round(float(limit_price), 2),
            time_in_force="day",
            metadata={"max_notional_usd": float(max_notional_usd)},
        )
    )
    return {
        "symbol": report.symbol,
        "side": report.side,
        "quantity": report.quantity,
        "status": report.status,
        "broker_order_id": report.broker_order_id,
        "raw_payload": report.raw_payload,
    }


def execute_value_target_plan(
    *,
    plan: dict[str, Any],
    market_data_port: MarketDataPort,
    execution_port: ExecutionPort,
    dry_run_only: bool,
    limit_sell_discount: float = 0.995,
    limit_buy_premium: float = 1.005,
    max_order_notional_usd: float = 25.0,
) -> ExecutionCycleResult:
    del dry_run_only  # ExecutionPort owns preview vs live submission.
    allocation = dict(plan.get("allocation") or {})
    portfolio = dict(plan.get("portfolio") or {})
    execution = dict(plan.get("execution") or {})
    targets = {str(k).upper(): float(v or 0.0) for k, v in dict(allocation.get("targets") or {}).items()}
    market_values = {
        str(k).upper(): float(v or 0.0)
        for k, v in dict(portfolio.get("market_values") or {}).items()
    }
    sellable_quantities = {
        str(k).upper(): float(v or 0.0)
        for k, v in dict(portfolio.get("sellable_quantities") or {}).items()
    }
    threshold = float(
        execution.get("current_min_trade")
        or execution.get("trade_threshold_value")
        or 0.0
    )
    investable_cash = max(
        0.0,
        float(execution.get("investable_cash") or portfolio.get("liquid_cash") or 0.0),
    )
    order_notional_cap = max(0.0, float(max_order_notional_usd or 0.0))

    submitted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    tradable_deltas: list[tuple[str, float, float]] = []
    for symbol in sorted(set(targets) | set(market_values)):
        target_value = float(targets.get(symbol, 0.0))
        current_value = float(market_values.get(symbol, 0.0))
        delta_value = target_value - current_value
        if abs(delta_value) < threshold:
            skipped.append(
                {
                    "symbol": symbol,
                    "reason": "below_trade_threshold",
                    "delta_value": round(delta_value, 2),
                }
            )
            continue
        price = _quote_price(market_data_port, symbol)
        if price is None:
            skipped.append({"symbol": symbol, "reason": "quote_unavailable"})
            continue
        tradable_deltas.append((symbol, delta_value, price))

    for symbol, delta_value, price in [item for item in tradable_deltas if item[1] < 0]:
        if delta_value < 0:
            sellable = sellable_quantities.get(symbol, 0.0)
            sell_budget = min(abs(delta_value), sellable * price, order_notional_cap)
            quantity = _floor_quantity(sell_budget / price)
            if quantity <= 0:
                skipped.append(
                    {
                        "symbol": symbol,
                        "reason": "sell_quantity_zero",
                        "max_order_notional_usd": round(order_notional_cap, 2),
                    }
                )
                continue
            submitted.append(
                _submit_order(
                    execution_port,
                    symbol=symbol,
                    side="sell",
                    quantity=quantity,
                    limit_price=price * float(limit_sell_discount),
                    max_notional_usd=max_order_notional_usd,
                )
            )
            continue

    for symbol, delta_value, price in [item for item in tradable_deltas if item[1] > 0]:
        buy_budget = min(float(delta_value), investable_cash, order_notional_cap)
        quantity = _floor_quantity(buy_budget / price)
        if quantity <= 0:
            skipped.append(
                {
                    "symbol": symbol,
                    "reason": "buy_quantity_zero",
                    "max_order_notional_usd": round(order_notional_cap, 2),
                }
            )
            continue
        submitted.append(
            _submit_order(
                execution_port,
                symbol=symbol,
                side="buy",
                quantity=quantity,
                limit_price=price * float(limit_buy_premium),
                max_notional_usd=max_order_notional_usd,
            )
        )
        investable_cash = max(0.0, investable_cash - (quantity * price))

    return ExecutionCycleResult(
        submitted_orders=tuple(submitted),
        skipped_orders=tuple(skipped),
        action_done=bool(submitted),
    )
