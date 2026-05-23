"""Dry-run-first value-target execution planning for FirstradePlatform."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quant_platform_kit.common.models import OrderIntent
from quant_platform_kit.common.ports import ExecutionPort, MarketDataPort
try:
    from quant_platform_kit.common.small_account_compatibility import (
        project_unbuyable_value_targets_to_cash,
    )
except ImportError:  # pragma: no cover - compatibility with older pinned shared wheels
    def project_unbuyable_value_targets_to_cash(
        target_values,
        prices,
        *,
        symbols=None,
        quantity_step=1.0,
    ):
        adjusted = {
            str(symbol or "").strip().upper(): float(value or 0.0)
            for symbol, value in dict(target_values or {}).items()
        }
        step = max(0.0, float(quantity_step or 0.0))
        if step <= 0.0:
            return adjusted, ()
        candidate_symbols = (
            tuple(adjusted)
            if symbols is None
            else tuple(dict.fromkeys(str(symbol or "").strip().upper() for symbol in symbols))
        )
        normalized_prices = {
            str(symbol or "").strip().upper(): float(price or 0.0)
            for symbol, price in dict(prices or {}).items()
        }
        substituted = []
        for symbol in candidate_symbols:
            target_value = max(0.0, float(adjusted.get(symbol, 0.0) or 0.0))
            price = max(0.0, float(normalized_prices.get(symbol, 0.0) or 0.0))
            if price > 0.0 and 0.0 < target_value < (price * step):
                adjusted[symbol] = 0.0
                substituted.append(symbol)
        return adjusted, tuple(dict.fromkeys(substituted))


@dataclass(frozen=True)
class ExecutionCycleResult:
    submitted_orders: tuple[dict[str, Any], ...]
    skipped_orders: tuple[dict[str, Any], ...]
    action_done: bool


DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD = 1000.0


def _floor_quantity(quantity: float) -> int:
    return max(0, int(float(quantity or 0.0)))


def _sell_budget(
    *,
    delta_value: float,
    target_value: float,
    sellable_quantity: float,
    price: float,
    order_notional_cap: float,
) -> float:
    sellable_notional = max(0.0, float(sellable_quantity or 0.0)) * max(0.0, float(price or 0.0))
    if sellable_notional <= 0.0:
        return 0.0
    value_delta_budget = max(0.0, abs(float(delta_value or 0.0)))
    position_budget = max(0.0, sellable_notional - max(0.0, float(target_value or 0.0)))
    return min(max(value_delta_budget, position_budget), sellable_notional, max(0.0, float(order_notional_cap or 0.0)))


def _safe_haven_cash_symbols(*, portfolio: dict[str, Any], allocation: dict[str, Any]) -> tuple[str, ...]:
    symbols: list[str] = []
    for symbol in allocation.get("safe_haven_symbols", ()):
        normalized = str(symbol or "").strip().upper()
        if normalized:
            symbols.append(normalized)
    cash_sweep_symbol = str(portfolio.get("cash_sweep_symbol") or "").strip().upper()
    if cash_sweep_symbol:
        symbols.append(cash_sweep_symbol)
    return tuple(dict.fromkeys(symbols))


def substitute_small_safe_haven_targets_with_cash(
    plan: dict[str, Any],
    *,
    threshold_usd: float = DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD,
) -> dict[str, Any]:
    """Return a plan whose small safe-haven target values are left as cash."""
    threshold = max(0.0, float(threshold_usd or 0.0))
    if threshold <= 0.0:
        return dict(plan or {})

    adjusted_plan = dict(plan or {})
    allocation = dict(adjusted_plan.get("allocation") or {})
    portfolio = dict(adjusted_plan.get("portfolio") or {})
    targets = {
        str(symbol).strip().upper(): float(value or 0.0)
        for symbol, value in dict(allocation.get("targets") or {}).items()
    }
    changed = False
    for symbol in _safe_haven_cash_symbols(portfolio=portfolio, allocation=allocation):
        target_value = float(targets.get(symbol, 0.0) or 0.0)
        if 0.0 < target_value < threshold:
            targets[symbol] = 0.0
            changed = True
    if changed:
        allocation["targets"] = targets
        adjusted_plan["allocation"] = allocation
    return adjusted_plan


def _quote_price(market_data_port: MarketDataPort, symbol: str) -> float | None:
    try:
        price = float(market_data_port.get_quote(symbol).last_price)
    except Exception:
        return None
    return price if price > 0 else None


def _apply_small_account_whole_share_compatibility(
    plan: dict[str, Any],
    *,
    market_data_port: MarketDataPort,
) -> dict[str, Any]:
    adjusted_plan = dict(plan or {})
    allocation = dict(adjusted_plan.get("allocation") or {})
    targets = dict(allocation.get("targets") or {})
    candidate_symbols = tuple(
        dict.fromkeys(
            tuple(allocation.get("risk_symbols", ()))
            + tuple(allocation.get("income_symbols", ()))
        )
    )
    if not candidate_symbols:
        safe_haven_symbols = set(allocation.get("safe_haven_symbols", ()))
        candidate_symbols = tuple(
            symbol for symbol in targets if symbol not in safe_haven_symbols
        )
    prices = {}
    for symbol in candidate_symbols:
        price = _quote_price(market_data_port, str(symbol).strip().upper())
        if price is not None:
            prices[str(symbol).strip().upper()] = price
    adjusted_targets, substituted = project_unbuyable_value_targets_to_cash(
        targets,
        prices,
        symbols=candidate_symbols,
        quantity_step=1.0,
    )
    allocation["targets"] = adjusted_targets
    if substituted:
        allocation["small_account_whole_share_substituted_symbols"] = substituted
    adjusted_plan["allocation"] = allocation
    return adjusted_plan


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
        "order_type": "limit",
        "limit_price": round(float(limit_price), 2),
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
    safe_haven_cash_substitute_threshold_usd: float = DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD,
) -> ExecutionCycleResult:
    del dry_run_only  # ExecutionPort owns preview vs live submission.
    plan = substitute_small_safe_haven_targets_with_cash(
        plan,
        threshold_usd=safe_haven_cash_substitute_threshold_usd,
    )
    plan = _apply_small_account_whole_share_compatibility(
        plan,
        market_data_port=market_data_port,
    )
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
            sell_budget = _sell_budget(
                delta_value=delta_value,
                target_value=targets.get(symbol, 0.0),
                sellable_quantity=sellable,
                price=price,
                order_notional_cap=order_notional_cap,
            )
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
