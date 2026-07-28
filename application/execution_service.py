"""Dry-run-first value-target execution planning for FirstradePlatform."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quant_platform_kit.common.order_status import compute_confirmed_sell_release_value
from quant_platform_kit.common.models import OrderIntent
from quant_platform_kit.common.ports import ExecutionPort, MarketDataPort
try:
    from quant_platform_kit.common.small_account_compatibility import (
        apply_small_account_cash_compatibility,
        build_small_account_allocation_drift_notes,
    )
except ImportError:  # pragma: no cover - compatibility with older pinned shared wheels
    @dataclass(frozen=True)
    class _SmallAccountCashCompatibilityResult:
        targets: dict[str, float]
        whole_share_substituted_symbols: tuple[str, ...]
        safe_haven_cash_substituted_symbols: tuple[str, ...]
        cash_substitution_notes: tuple[dict[str, Any], ...]

    def _project_unbuyable_value_targets_to_cash(
        target_values,
        prices,
        *,
        candidate_symbols=None,
        quantity_step=1.0,
    ):
        adjusted = {
            str(symbol or "").strip().upper(): float(value or 0.0)
            for symbol, value in dict(target_values or {}).items()
        }
        step = max(0.0, float(quantity_step or 0.0))
        if step <= 0.0:
            return adjusted, ()
        normalized_candidates = (
            tuple(adjusted)
            if candidate_symbols is None
            else tuple(dict.fromkeys(str(symbol or "").strip().upper() for symbol in candidate_symbols))
        )
        normalized_prices = {
            str(symbol or "").strip().upper(): float(price or 0.0)
            for symbol, price in dict(prices or {}).items()
        }
        substituted = []
        for symbol in normalized_candidates:
            target_value = max(0.0, float(adjusted.get(symbol, 0.0) or 0.0))
            price = max(0.0, float(normalized_prices.get(symbol, 0.0) or 0.0))
            if price > 0.0 and 0.0 < target_value < (price * step):
                adjusted[symbol] = 0.0
                substituted.append(symbol)
        return adjusted, tuple(dict.fromkeys(substituted))

    def apply_small_account_cash_compatibility(
        target_values,
        prices,
        *,
        candidate_symbols=None,
        safe_haven_cash_symbols=(),
        quantity_step=1.0,
        cash_substitute_limit_usd=2000.0,
    ):
        adjusted_targets, substituted = _project_unbuyable_value_targets_to_cash(
            target_values,
            prices,
            candidate_symbols=candidate_symbols,
            quantity_step=quantity_step,
        )
        normalized_candidates = (
            tuple(adjusted_targets)
            if candidate_symbols is None
            else tuple(dict.fromkeys(str(symbol or "").strip().upper() for symbol in candidate_symbols))
        )
        remaining_non_safe_targets = [
            symbol
            for symbol in normalized_candidates
            if float(adjusted_targets.get(str(symbol or "").strip().upper(), 0.0) or 0.0) > 0.0
        ]
        safe_haven_symbols = tuple(
            dict.fromkeys(
                str(symbol or "").strip().upper()
                for symbol in safe_haven_cash_symbols
                if str(symbol or "").strip()
            )
        )
        safe_haven_substituted = []
        if (
            substituted
            and not remaining_non_safe_targets
            and _positive_target_total(adjusted_targets) <= max(0.0, float(cash_substitute_limit_usd or 0.0))
        ):
            for symbol in safe_haven_symbols:
                if float(adjusted_targets.get(symbol, 0.0) or 0.0) > 0.0:
                    adjusted_targets[symbol] = 0.0
                    safe_haven_substituted.append(symbol)
        normalized_targets = {
            str(symbol or "").strip().upper(): float(value or 0.0)
            for symbol, value in dict(target_values or {}).items()
        }
        normalized_prices = {
            str(symbol or "").strip().upper(): float(price or 0.0)
            for symbol, price in dict(prices or {}).items()
        }
        notes = []
        if safe_haven_substituted:
            for symbol in substituted:
                target_value = max(0.0, float(normalized_targets.get(symbol, 0.0) or 0.0))
                price = max(0.0, float(normalized_prices.get(symbol, 0.0) or 0.0))
                if target_value <= 0.0 or price <= 0.0:
                    continue
                notes.append(
                    {
                        "symbol": symbol,
                        "target_value": target_value,
                        "price": price,
                        "cash_symbols": tuple(safe_haven_substituted),
                    }
                )
        return _SmallAccountCashCompatibilityResult(
            targets=adjusted_targets,
            whole_share_substituted_symbols=substituted,
            safe_haven_cash_substituted_symbols=tuple(safe_haven_substituted),
            cash_substitution_notes=tuple(notes),
        )

    def build_small_account_allocation_drift_notes(**_kwargs):
        return ()

@dataclass(frozen=True)
class ExecutionCycleResult:
    submitted_orders: tuple[dict[str, Any], ...]
    skipped_orders: tuple[dict[str, Any], ...]
    action_done: bool
    execution_notes: tuple[dict[str, Any], ...] = ()


DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD = 1000.0
SMALL_ACCOUNT_SAFE_HAVEN_CASH_SUBSTITUTE_LIMIT_USD = 2000.0
MIN_NOTIONAL_BUY_USD = 1.0
_ACCEPTED_ORDER_STATUSES = frozenset(
    {"accepted", "filled", "partiallyfilled", "previewed", "submitted"}
)
_BROKER_REJECTION_SKIP_REASONS = frozenset(
    {"broker_rejected", "fractional_trading_disclosure_required"}
)
SMALL_ACCOUNT_EXISTING_WHOLE_SHARE_RETENTION_SYMBOLS = frozenset({"TQQQ", "SOXL"})
_SMALL_ACCOUNT_RETENTION_MIN_TARGET_SHARE_RATIO_DEFAULT = 0.85
SMALL_ACCOUNT_EXISTING_WHOLE_SHARE_RETENTION_MIN_TARGET_SHARE_RATIO_BY_SYMBOL = {
    "SOXX": 0.90,
}
SMALL_ACCOUNT_WHOLE_SHARE_BOOTSTRAP_MIN_TARGET_SHARE_RATIO_BY_SYMBOL = {
    "TQQQ": 0.90,
    "SOXL": 0.90,
    "SOXX": 0.90,
}


def _limit_buy_premium_for_symbol(symbol, default_premium, premium_by_symbol=None) -> float:
    normalized_symbol = str(symbol or "").strip().upper()
    try:
        fallback = float(default_premium)
    except (TypeError, ValueError):
        fallback = 1.005
    if not isinstance(premium_by_symbol, dict):
        return fallback
    raw_value = premium_by_symbol.get(normalized_symbol)
    if raw_value is None:
        return fallback
    try:
        premium = float(raw_value)
    except (TypeError, ValueError):
        return fallback
    return premium if premium > 0.0 else fallback


def _limit_buy_price(symbol, price, default_premium, premium_by_symbol=None) -> float:
    return round(
        float(price) * _limit_buy_premium_for_symbol(symbol, default_premium, premium_by_symbol),
        2,
    )


def _floor_quantity(quantity: float) -> int:
    return max(0, int(float(quantity or 0.0)))


def _sell_budget(
    *,
    delta_value: float,
    target_value: float,
    sellable_quantity: float,
    price: float,
    order_notional_cap: float | None,
) -> float:
    sellable_notional = max(0.0, float(sellable_quantity or 0.0)) * max(0.0, float(price or 0.0))
    if sellable_notional <= 0.0:
        return 0.0
    value_delta_budget = max(0.0, abs(float(delta_value or 0.0)))
    position_budget = max(0.0, sellable_notional - max(0.0, float(target_value or 0.0)))
    budget = min(max(value_delta_budget, position_budget), sellable_notional)
    if order_notional_cap is not None:
        budget = min(budget, max(0.0, float(order_notional_cap or 0.0)))
    return budget


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


def _small_account_drift_reference_targets(
    allocation: dict[str, Any],
    *,
    portfolio: dict[str, Any] | None = None,
) -> dict[str, float]:
    allocation = dict(allocation or {})
    targets = {
        str(symbol or "").strip().upper(): float(value or 0.0)
        for symbol, value in dict(allocation.get("targets") or {}).items()
    }
    candidate_symbols = tuple(
        dict.fromkeys(
            str(symbol or "").strip().upper()
            for symbol in tuple(allocation.get("risk_symbols", ()))
            + tuple(allocation.get("income_symbols", ()))
            if str(symbol or "").strip()
        )
    )
    if not candidate_symbols:
        safe_haven_symbols = set(_safe_haven_cash_symbols(portfolio=dict(portfolio or {}), allocation=allocation))
        candidate_symbols = tuple(symbol for symbol in targets if symbol not in safe_haven_symbols)
    return {symbol: targets.get(symbol, 0.0) for symbol in candidate_symbols if symbol in targets}


def _positive_target_total(targets: dict[str, Any]) -> float:
    total = 0.0
    for value in dict(targets or {}).values():
        try:
            total += max(0.0, float(value or 0.0))
        except (TypeError, ValueError):
            continue
    return total


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


def _should_retain_existing_whole_share(symbol, *, target_value, price, quantity=0.0) -> bool:
    """Decide whether an existing whole-share position should be retained.

    Universal rule: if the account already holds this symbol (>0 shares) and the
    strategy wants to keep a meaningful fraction of a share (target >= 85% of
    1-share price), retain the position.  This prevents the sell-then-fail-to-rebuy
    cycle for small accounts where target < 1 share but still close to it.

    Genuine reductions (target << 1 share) are NOT blocked.
    The hardcoded lists act as overrides for symbols that need a different threshold.
    """
    normalized_symbol = str(symbol or "").strip().upper()
    held = float(quantity or 0.0)
    target = float(target_value or 0.0)
    quote_price = max(0.0, float(price or 0.0))

    # Universal: held + positive target + target close to 1-share price -> retain
    if held > 0.0 and target > 0.0 and quote_price > 0.0:
        if target >= quote_price * _SMALL_ACCOUNT_RETENTION_MIN_TARGET_SHARE_RATIO_DEFAULT:
            return True

    # Legacy whitelist -- unconditional retention (safety net)
    if normalized_symbol in SMALL_ACCOUNT_EXISTING_WHOLE_SHARE_RETENTION_SYMBOLS:
        return True

    # Legacy per-symbol ratio-based retention (override / tighter threshold)
    min_target_share_ratio = (
        SMALL_ACCOUNT_EXISTING_WHOLE_SHARE_RETENTION_MIN_TARGET_SHARE_RATIO_BY_SYMBOL.get(normalized_symbol)
    )
    if min_target_share_ratio is None:
        return False
    if quote_price <= 0.0:
        return False
    return target >= quote_price * float(min_target_share_ratio)


def _should_bootstrap_whole_share_buy(symbol, *, target_value, limit_price) -> bool:
    normalized_symbol = str(symbol or "").strip().upper()
    min_target_share_ratio = (
        SMALL_ACCOUNT_WHOLE_SHARE_BOOTSTRAP_MIN_TARGET_SHARE_RATIO_BY_SYMBOL.get(normalized_symbol)
    )
    if min_target_share_ratio is None:
        return False
    effective_limit_price = max(0.0, float(limit_price or 0.0))
    if effective_limit_price <= 0.0:
        return False
    return max(0.0, float(target_value or 0.0)) >= effective_limit_price * float(min_target_share_ratio)


def _should_top_up_existing_whole_share_buy(
    symbol,
    *,
    target_value,
    current_value,
    quantity=0.0,
    limit_price,
) -> bool:
    del symbol
    held_quantity = max(0.0, float(quantity or 0.0))
    effective_limit_price = max(0.0, float(limit_price or 0.0))
    if held_quantity < 1.0 or effective_limit_price <= 0.0:
        return False
    remaining_value = max(0.0, float(target_value or 0.0) - float(current_value or 0.0))
    if remaining_value <= 0.0 or remaining_value >= effective_limit_price:
        return False
    held_whole_shares = int(held_quantity)
    if held_whole_shares <= 0:
        return False
    target_quantity = max(0.0, float(target_value or 0.0)) / effective_limit_price
    return target_quantity >= held_whole_shares + 0.5


def _planned_buy_order_quantity(
    symbol,
    *,
    target_value,
    current_value,
    quantity=0.0,
    buy_budget,
    available_buying_power,
    limit_price,
    allow_top_up=False,
) -> int:
    effective_limit_price = max(0.0, float(limit_price or 0.0))
    if effective_limit_price <= 0.0:
        return 0
    planned_quantity = _floor_quantity(max(0.0, float(buy_budget or 0.0)) / effective_limit_price)
    if planned_quantity > 0:
        return planned_quantity
    if not allow_top_up:
        return 0
    if max(0.0, float(available_buying_power or 0.0)) < effective_limit_price:
        return 0
    if _should_top_up_existing_whole_share_buy(
        symbol,
        target_value=target_value,
        current_value=current_value,
        quantity=quantity,
        limit_price=effective_limit_price,
    ):
        return 1
    return 0


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
    limit_buy_premium: float = 1.005,
    limit_buy_premium_by_symbol: dict[str, float] | None = None,
) -> dict[str, Any]:
    adjusted_plan = dict(plan or {})
    allocation = dict(adjusted_plan.get("allocation") or {})
    portfolio = dict(adjusted_plan.get("portfolio") or {})
    targets = dict(allocation.get("targets") or {})
    candidate_symbols = tuple(
        dict.fromkeys(
            str(symbol or "").strip().upper()
            for symbol in tuple(allocation.get("risk_symbols", ()))
            + tuple(allocation.get("income_symbols", ()))
            if str(symbol or "").strip()
        )
    )
    if not candidate_symbols:
        safe_haven_symbols = set(_safe_haven_cash_symbols(portfolio=portfolio, allocation=allocation))
        candidate_symbols = tuple(
            str(symbol or "").strip().upper()
            for symbol in targets
            if str(symbol or "").strip().upper() not in safe_haven_symbols
        )
    prices = {}
    for symbol in candidate_symbols:
        price = _quote_price(market_data_port, str(symbol).strip().upper())
        if price is not None:
            prices[str(symbol).strip().upper()] = price
    retained_symbols = []
    bootstrap_symbols = []
    quantities = {
        str(symbol or "").strip().upper(): float(quantity or 0.0)
        for symbol, quantity in dict(portfolio.get("quantities") or {}).items()
    }
    execution = dict(adjusted_plan.get("execution") or {})
    _liquid_cash = float(portfolio.get("liquid_cash", 0.0) or 0.0)
    _reserved_cash = float(execution.get("reserved_cash", 0.0) or 0.0)
    _estimated_buying_power = max(0.0, _liquid_cash - _reserved_cash)
    compatibility_targets = {
        str(symbol or "").strip().upper(): float(value or 0.0)
        for symbol, value in targets.items()
    }
    for symbol in candidate_symbols:
        target_value = max(0.0, float(compatibility_targets.get(symbol, 0.0) or 0.0))
        price = max(0.0, float(prices.get(symbol, 0.0) or 0.0))
        limit_price = (
            _limit_buy_price(symbol, price, limit_buy_premium, limit_buy_premium_by_symbol)
            if price > 0.0
            else 0.0
        )
        # Skip bootstrap if the account cannot afford even 1 share at limit price.
        _can_afford_one_share = limit_price > 0.0 and _estimated_buying_power >= limit_price
        if not _should_retain_existing_whole_share(symbol, target_value=target_value, price=price, quantity=quantities.get(symbol, 0.0)):
            if (
                quantities.get(symbol, 0.0) <= 0.0
                and 0.0 < target_value < limit_price
                and _can_afford_one_share
                and _should_bootstrap_whole_share_buy(symbol, target_value=target_value, limit_price=limit_price)
            ):
                compatibility_targets[symbol] = limit_price
                bootstrap_symbols.append(symbol)
            continue
        if price > 0.0 and 0.0 < target_value < price and quantities.get(symbol, 0.0) >= 1.0:
            compatibility_targets[symbol] = price
            retained_symbols.append(symbol)
            continue
        if (
            quantities.get(symbol, 0.0) <= 0.0
            and 0.0 < target_value < limit_price
            and _can_afford_one_share
            and _should_bootstrap_whole_share_buy(symbol, target_value=target_value, limit_price=limit_price)
        ):
            compatibility_targets[symbol] = limit_price
            bootstrap_symbols.append(symbol)
    safe_haven_symbols = _safe_haven_cash_symbols(portfolio=portfolio, allocation=allocation)
    compatibility = apply_small_account_cash_compatibility(
        compatibility_targets,
        prices,
        candidate_symbols=candidate_symbols,
        safe_haven_cash_symbols=safe_haven_symbols,
        quantity_step=1.0,
        cash_substitute_limit_usd=SMALL_ACCOUNT_SAFE_HAVEN_CASH_SUBSTITUTE_LIMIT_USD,
    )
    allocation["targets"] = compatibility.targets
    substituted = compatibility.whole_share_substituted_symbols
    safe_haven_substituted = compatibility.safe_haven_cash_substituted_symbols
    allocation.pop("small_account_whole_share_cash_notes", None)
    if substituted:
        allocation["small_account_whole_share_substituted_symbols"] = substituted
    if safe_haven_substituted:
        allocation["small_account_safe_haven_cash_substituted_symbols"] = tuple(safe_haven_substituted)
    if retained_symbols:
        allocation["small_account_existing_whole_share_retained_symbols"] = tuple(
            dict.fromkeys(retained_symbols)
        )
    if bootstrap_symbols:
        allocation["small_account_whole_share_bootstrap_symbols"] = tuple(
            dict.fromkeys(bootstrap_symbols)
        )
    if compatibility.cash_substitution_notes:
        allocation["small_account_whole_share_cash_notes"] = tuple(compatibility.cash_substitution_notes)
    adjusted_plan["allocation"] = allocation
    return adjusted_plan


def _submit_order(
    execution_port: ExecutionPort,
    *,
    symbol: str,
    side: str,
    quantity: int,
    limit_price: float,
    max_notional_usd: float | None,
) -> dict[str, Any]:
    metadata = {}
    if max_notional_usd is not None:
        metadata["max_notional_usd"] = float(max_notional_usd)
    report = execution_port.submit_order(
        OrderIntent(
            symbol=symbol,
            side=side,
            quantity=float(quantity),
            order_type="limit",
            limit_price=round(float(limit_price), 2),
            time_in_force="day",
            metadata=metadata,
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


def _submit_notional_buy_order(
    execution_port: ExecutionPort,
    *,
    symbol: str,
    notional_usd: float,
    max_notional_usd: float | None,
) -> dict[str, Any]:
    metadata = {"notional_usd": round(float(notional_usd), 2)}
    if max_notional_usd is not None:
        metadata["max_notional_usd"] = float(max_notional_usd)
    report = execution_port.submit_order(
        OrderIntent(
            symbol=symbol,
            side="buy",
            quantity=0.0,
            order_type="market",
            time_in_force="day",
            metadata=metadata,
        )
    )
    return {
        "symbol": report.symbol,
        "side": report.side,
        "quantity": report.quantity,
        "order_type": "market",
        "notional_usd": round(float(notional_usd), 2),
        "status": report.status,
        "broker_order_id": report.broker_order_id,
        "raw_payload": report.raw_payload,
    }


def _order_submission_accepted(order: dict[str, Any]) -> bool:
    status = "".join(
        ch for ch in str(order.get("status") or "").strip().lower() if ch.isalnum()
    )
    return status in _ACCEPTED_ORDER_STATUSES


def _broker_rejection_reason(order: dict[str, Any]) -> str:
    raw_payload = dict(order.get("raw_payload") or {})
    for key, value in raw_payload.items():
        normalized = "".join(ch for ch in str(key).lower() if ch.isalnum())
        if normalized == "refcode" and str(value or "").strip() == "1219":
            return "fractional_trading_disclosure_required"
    return "broker_rejected"


def _record_order_result(
    order: dict[str, Any],
    *,
    submitted: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
) -> bool:
    if _order_submission_accepted(order):
        submitted.append(order)
        return True
    skipped.append({**order, "reason": _broker_rejection_reason(order)})
    return False


def _orders_for_allocation_drift(
    submitted_orders: list[dict[str, Any]],
    *,
    prices: dict[str, float],
) -> list[dict[str, Any]]:
    projected_orders = []
    for order in submitted_orders:
        projected_order = dict(order)
        notional_usd = float(projected_order.get("notional_usd") or 0.0)
        symbol = str(projected_order.get("symbol") or "").strip().upper()
        price = float(prices.get(symbol) or 0.0)
        if notional_usd > 0.0 and price > 0.0:
            projected_order["quantity"] = notional_usd / price
        projected_orders.append(projected_order)
    return projected_orders


def execute_value_target_plan(
    *,
    plan: dict[str, Any],
    market_data_port: MarketDataPort,
    execution_port: ExecutionPort,
    dry_run_only: bool,
    limit_sell_discount: float = 0.995,
    limit_buy_premium: float = 1.005,
    limit_buy_premium_by_symbol: dict[str, float] | None = None,
    max_order_notional_usd: float | None = None,
    safe_haven_cash_substitute_threshold_usd: float = DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD,
    cash_only_execution: bool = True,
    notional_buy_execution: bool = False,
    fetch_order_status=None,
) -> ExecutionCycleResult:
    del dry_run_only  # ExecutionPort owns preview vs live submission.
    plan = substitute_small_safe_haven_targets_with_cash(
        plan,
        threshold_usd=safe_haven_cash_substitute_threshold_usd,
    )
    plan_portfolio = dict(plan.get("portfolio") or {})
    small_account_reference_target_values = _small_account_drift_reference_targets(
        dict(plan.get("allocation") or {}),
        portfolio=plan_portfolio,
    )
    if not notional_buy_execution:
        plan = _apply_small_account_whole_share_compatibility(
            plan,
            market_data_port=market_data_port,
            limit_buy_premium=limit_buy_premium,
            limit_buy_premium_by_symbol=limit_buy_premium_by_symbol,
        )
    allocation = dict(plan.get("allocation") or {})
    portfolio = dict(plan.get("portfolio") or {})
    execution = dict(plan.get("execution") or {})
    execution_notes = tuple(allocation.get("small_account_whole_share_cash_notes") or ())
    targets = {str(k).upper(): float(v or 0.0) for k, v in dict(allocation.get("targets") or {}).items()}
    market_values = {
        str(k).upper(): float(v or 0.0)
        for k, v in dict(portfolio.get("market_values") or {}).items()
    }
    sellable_quantities = {
        str(k).upper(): float(v or 0.0)
        for k, v in dict(portfolio.get("sellable_quantities") or {}).items()
    }
    current_quantities = {
        str(k).upper(): float(v or 0.0)
        for k, v in dict(portfolio.get("quantities") or sellable_quantities).items()
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
    raw_liquid_cash = float(portfolio.get("liquid_cash") or 0.0)
    order_notional_cap = (
        max(0.0, float(max_order_notional_usd))
        if max_order_notional_usd is not None and float(max_order_notional_usd) > 0.0
        else None
    )

    submitted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    reference_prices: dict[str, float] = {}
    pending_sell_release_symbols: list[str] = []
    submitted_sell_orders: list[dict[str, Any]] = []
    sell_submitted = False

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
        reference_prices[symbol] = price
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
                pending_sell_release_symbols.append(symbol)
                skipped.append(
                    {
                        "symbol": symbol,
                        "reason": "sell_quantity_zero",
                        **(
                            {"max_order_notional_usd": round(order_notional_cap, 2)}
                            if order_notional_cap is not None
                            else {}
                        ),
                    }
                )
                continue
            sell_limit_price = price * float(limit_sell_discount)
            order_result = _submit_order(
                execution_port,
                symbol=symbol,
                side="sell",
                quantity=quantity,
                limit_price=sell_limit_price,
                max_notional_usd=max_order_notional_usd,
            )
            pending_sell_release_symbols.append(symbol)
            if _record_order_result(
                order_result,
                submitted=submitted,
                skipped=skipped,
            ):
                submitted_sell_orders.append(order_result)
                sell_submitted = True
            continue

    confirmed_sell_release_value = compute_confirmed_sell_release_value(
        submitted_sell_orders=submitted_sell_orders,
        fetch_order_status=fetch_order_status,
    )
    investable_cash = max(
        0.0,
        float(execution.get("investable_cash") or portfolio.get("liquid_cash") or 0.0)
        + confirmed_sell_release_value,
    )

    buy_deltas = [item for item in tradable_deltas if item[1] > 0]
    _buys_blocked_reason: str | None = None
    if cash_only_execution and buy_deltas and pending_sell_release_symbols:
        estimated_buy_cost = 0.0
        requires_pending_sell_release = False
        for symbol, delta_value, price in buy_deltas:
            buy_budget = min(float(delta_value), investable_cash)
            if order_notional_cap is not None:
                buy_budget = min(buy_budget, order_notional_cap)
            if notional_buy_execution:
                if buy_budget >= MIN_NOTIONAL_BUY_USD:
                    estimated_buy_cost += buy_budget
                elif float(delta_value) >= MIN_NOTIONAL_BUY_USD:
                    requires_pending_sell_release = True
            else:
                limit_price = _limit_buy_price(
                    symbol, price, limit_buy_premium, limit_buy_premium_by_symbol
                )
                quantity = _planned_buy_order_quantity(
                    symbol,
                    target_value=delta_value + market_values.get(symbol, 0.0),
                    current_value=market_values.get(symbol, 0.0),
                    quantity=current_quantities.get(symbol, 0.0),
                    buy_budget=buy_budget,
                    available_buying_power=investable_cash if order_notional_cap is None else min(investable_cash, order_notional_cap),
                    limit_price=limit_price,
                    allow_top_up=sell_submitted,
                )
                if quantity > 0:
                    estimated_buy_cost += quantity * limit_price
                elif float(delta_value) > 0.0 and limit_price > max(0.0, buy_budget):
                    requires_pending_sell_release = True
        if estimated_buy_cost > investable_cash or requires_pending_sell_release:
            _buys_blocked_reason = "pending_sell_release"
            for symbol, _delta_value, _price in buy_deltas:
                skipped.append(
                    {
                        "symbol": symbol,
                        "reason": "pending_sell_release",
                        "pending_sell_symbols": list(pending_sell_release_symbols),
                    }
                )
            buy_deltas = []
    elif cash_only_execution and buy_deltas and raw_liquid_cash < 0.0:
        _buys_blocked_reason = "negative_cash"
        for symbol, _delta_value, _price in buy_deltas:
            skipped.append(
                {
                    "symbol": symbol,
                    "reason": "negative_cash",
                    "liquid_cash": round(raw_liquid_cash, 2),
                }
            )
        buy_deltas = []

    for symbol, delta_value, price in buy_deltas:
        buy_budget = min(float(delta_value), investable_cash)
        if order_notional_cap is not None:
            buy_budget = min(buy_budget, order_notional_cap)
        if notional_buy_execution:
            if buy_budget < MIN_NOTIONAL_BUY_USD:
                skipped.append(
                    {
                        "symbol": symbol,
                        "reason": "buy_notional_below_minimum",
                        "notional_usd": round(buy_budget, 2),
                        "min_notional_usd": MIN_NOTIONAL_BUY_USD,
                    }
                )
                continue
            order_result = _submit_notional_buy_order(
                execution_port,
                symbol=symbol,
                notional_usd=buy_budget,
                max_notional_usd=max_order_notional_usd,
            )
            if _record_order_result(
                order_result,
                submitted=submitted,
                skipped=skipped,
            ):
                investable_cash = max(0.0, investable_cash - buy_budget)
            continue
        limit_price = _limit_buy_price(symbol, price, limit_buy_premium, limit_buy_premium_by_symbol)
        quantity = _planned_buy_order_quantity(
            symbol,
            target_value=targets.get(symbol, 0.0),
            current_value=market_values.get(symbol, 0.0),
            quantity=current_quantities.get(symbol, 0.0),
            buy_budget=buy_budget,
            available_buying_power=investable_cash if order_notional_cap is None else min(investable_cash, order_notional_cap),
            limit_price=limit_price,
            allow_top_up=sell_submitted,
        )
        if quantity <= 0:
            if order_notional_cap is None and investable_cash < limit_price:
                skipped.append(
                    {
                        "symbol": symbol,
                        "reason": "insufficient_cash_for_whole_share",
                        "price": round(limit_price, 2),
                        "investable_cash": round(investable_cash, 2),
                        "required_cash_for_one_share": round(limit_price, 2),
                    }
                )
            else:
                skipped.append(
                    {
                        "symbol": symbol,
                        "reason": "buy_quantity_zero",
                        **(
                            {"max_order_notional_usd": round(order_notional_cap, 2)}
                            if order_notional_cap is not None
                            else {}
                        ),
                    }
                )
            continue
        order_result = _submit_order(
            execution_port,
            symbol=symbol,
            side="buy",
            quantity=quantity,
            limit_price=limit_price,
            max_notional_usd=max_order_notional_usd,
        )
        if _record_order_result(
            order_result,
            submitted=submitted,
            skipped=skipped,
        ):
            investable_cash = max(0.0, investable_cash - (quantity * limit_price))

    total_value = float(portfolio.get("total_equity") or portfolio.get("total_strategy_equity") or 0.0)
    has_broker_rejection = any(
        str(item.get("reason") or "") in _BROKER_REJECTION_SKIP_REASONS
        for item in skipped
    )
    drift_notes = (
        ()
        if has_broker_rejection
        else build_small_account_allocation_drift_notes(
            target_values=small_account_reference_target_values,
            current_values=market_values,
            current_quantities=current_quantities,
            prices=reference_prices,
            submitted_orders=_orders_for_allocation_drift(
                submitted,
                prices=reference_prices,
            ),
            total_value=total_value,
            cash_value=float(portfolio.get("liquid_cash") or 0.0),
        )
    )
    execution_notes = tuple(execution_notes) + tuple(drift_notes)

    return ExecutionCycleResult(
        submitted_orders=tuple(submitted),
        skipped_orders=tuple(skipped),
        action_done=bool(submitted),
        execution_notes=execution_notes,
    )
