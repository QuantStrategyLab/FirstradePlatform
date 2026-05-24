from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from quant_platform_kit.strategy_contracts import (
    PositionTarget,
    StrategyDecision,
    ValueTargetExecutionAnnotations,
    build_value_target_execution_annotations,
    build_value_target_portfolio_inputs_from_snapshot,
    build_value_target_runtime_plan,
    resolve_decision_target_mode,
    translate_decision_to_target_mode,
)
from us_equity_strategies.catalog import resolve_canonical_profile

_SAFE_HAVEN_SYMBOLS = frozenset({"BOXX", "BIL"})
_INCOME_SYMBOLS = frozenset({"QQQI", "SPYI"})
_DEFAULT_MIN_TRADE_FLOOR = 100.0
_DEFAULT_REBALANCE_THRESHOLD_RATIO = 0.01


def _symbol_role(symbol: str) -> str | None:
    normalized = str(symbol or "").strip().upper()
    if normalized in _SAFE_HAVEN_SYMBOLS:
        return "safe_haven"
    if normalized in _INCOME_SYMBOLS:
        return "income"
    return None


def _default_threshold_value(total_equity: float) -> float:
    return max(_DEFAULT_MIN_TRADE_FLOOR, float(total_equity) * _DEFAULT_REBALANCE_THRESHOLD_RATIO)


def _resolve_platform_reserved_cash(
    *,
    total_equity: float,
    runtime_metadata: Mapping[str, Any] | None,
) -> float:
    raw_policy = (runtime_metadata or {}).get("firstrade_execution_policy")
    if not isinstance(raw_policy, Mapping):
        return 0.0
    reserved_cash_floor_usd = max(0.0, float(raw_policy.get("reserved_cash_floor_usd", 0.0) or 0.0))
    reserved_cash_ratio = float(raw_policy.get("reserved_cash_ratio", 0.0) or 0.0)
    reserved_cash_ratio = max(0.0, min(1.0, reserved_cash_ratio))
    return max(reserved_cash_floor_usd, max(0.0, float(total_equity)) * reserved_cash_ratio)


def _apply_reserved_cash_policy(
    annotations: ValueTargetExecutionAnnotations,
    *,
    portfolio_inputs,
    runtime_metadata: Mapping[str, Any] | None,
) -> ValueTargetExecutionAnnotations:
    reserved_cash = max(
        float(annotations.reserved_cash or 0.0),
        _resolve_platform_reserved_cash(
            total_equity=float(portfolio_inputs.total_equity),
            runtime_metadata=runtime_metadata,
        ),
    )
    base_investable_cash = annotations.investable_cash
    if base_investable_cash is None:
        base_investable_cash = max(
            0.0,
            float(portfolio_inputs.liquid_cash) - float(annotations.reserved_cash or 0.0),
        )
    investable_cash = min(
        max(0.0, float(base_investable_cash)),
        max(0.0, float(portfolio_inputs.liquid_cash) - reserved_cash),
    )
    return replace(
        annotations,
        reserved_cash=reserved_cash,
        investable_cash=investable_cash,
    )


def _build_hold_current_value_decision(portfolio_inputs, *, diagnostics: Mapping[str, Any]) -> StrategyDecision:
    positions = []
    for symbol, market_value in sorted(portfolio_inputs.market_values.items()):
        positions.append(
            PositionTarget(
                symbol=str(symbol),
                target_value=float(market_value),
                role=_symbol_role(str(symbol)),
            )
        )
    return StrategyDecision(
        positions=tuple(positions),
        risk_flags=frozenset({"no_execute"}),
        diagnostics=dict(diagnostics),
    )


def _build_weight_translation_annotations(
    decision: StrategyDecision,
    *,
    total_equity: float,
    liquid_cash: float,
    runtime_metadata: Mapping[str, Any] | None = None,
) -> ValueTargetExecutionAnnotations:
    diagnostics = {**dict(runtime_metadata or {}), **dict(decision.diagnostics)}
    execution_annotations = {}
    for source in (
        (runtime_metadata or {}).get("execution_annotations"),
        diagnostics.get("execution_annotations"),
    ):
        if isinstance(source, Mapping):
            execution_annotations.update(source)
    threshold_value = _default_threshold_value(total_equity)
    signal_display = str(
        diagnostics.get("signal_description")
        or diagnostics.get("signal_display")
        or diagnostics.get("signal_message")
        or ""
    ).strip()
    status_display = str(
        diagnostics.get("status_description")
        or diagnostics.get("market_status")
        or diagnostics.get("canary_status")
        or ""
    ).strip()
    return ValueTargetExecutionAnnotations(
        trade_threshold_value=threshold_value,
        reserved_cash=0.0,
        signal_display=signal_display,
        status_display=status_display,
        dashboard_text=str(execution_annotations.get("dashboard_text") or diagnostics.get("dashboard") or ""),
        signal_date=str(execution_annotations.get("signal_date") or diagnostics.get("signal_date") or ""),
        effective_date=str(execution_annotations.get("effective_date") or diagnostics.get("effective_date") or ""),
        execution_timing_contract=str(
            execution_annotations.get("execution_timing_contract")
            or diagnostics.get("execution_timing_contract")
            or ""
        ),
        execution_calendar_source=str(
            execution_annotations.get("execution_calendar_source")
            or diagnostics.get("execution_calendar_source")
            or ""
        ),
        signal_effective_after_trading_days=(
            int(signal_delay)
            if (
                signal_delay := execution_annotations.get(
                    "signal_effective_after_trading_days",
                    diagnostics.get("signal_effective_after_trading_days"),
                )
            )
            is not None
            else None
        ),
        benchmark_symbol=str(diagnostics.get("benchmark_symbol") or "").strip().upper() or None,
        benchmark_price=(
            float(diagnostics["benchmark_price"])
            if diagnostics.get("benchmark_price") is not None
            else None
        ),
        long_trend_value=(
            float(diagnostics["long_trend_value"])
            if diagnostics.get("long_trend_value") is not None
            else None
        ),
        exit_line=(
            float(diagnostics["exit_line"])
            if diagnostics.get("exit_line") is not None
            else None
        ),
        current_min_trade=threshold_value,
        investable_cash=max(0.0, float(liquid_cash)),
    )


def _normalize_to_value_decision(
    decision: StrategyDecision,
    *,
    portfolio_inputs,
    runtime_metadata: Mapping[str, Any] | None,
) -> tuple[StrategyDecision, ValueTargetExecutionAnnotations | None]:
    target_mode = resolve_decision_target_mode(decision)
    no_execute = "no_execute" in set(decision.risk_flags)
    if target_mode == "value" and not no_execute:
        return decision, None
    if target_mode == "weight" and not no_execute:
        translated = translate_decision_to_target_mode(
            decision,
            target_mode="value",
            total_equity=float(portfolio_inputs.total_equity),
        )
        return translated, _build_weight_translation_annotations(
            decision,
            total_equity=float(portfolio_inputs.total_equity),
            liquid_cash=float(portfolio_inputs.liquid_cash),
            runtime_metadata=runtime_metadata,
        )
    diagnostics = {**dict(runtime_metadata or {}), **dict(decision.diagnostics)}
    return _build_hold_current_value_decision(portfolio_inputs, diagnostics=diagnostics), None


def _build_annotations(decision: StrategyDecision, *, portfolio_inputs) -> ValueTargetExecutionAnnotations:
    annotations = build_value_target_execution_annotations(decision)
    investable_cash = annotations.investable_cash
    if investable_cash is None:
        investable_cash = max(0.0, float(portfolio_inputs.liquid_cash) - annotations.reserved_cash)
    current_min_trade = annotations.current_min_trade
    if current_min_trade is None:
        current_min_trade = annotations.trade_threshold_value
    return ValueTargetExecutionAnnotations(
        trade_threshold_value=annotations.trade_threshold_value,
        reserved_cash=annotations.reserved_cash,
        signal_display=annotations.signal_display,
        status_display=annotations.status_display,
        dashboard_text=annotations.dashboard_text,
        separator=annotations.separator,
        benchmark_symbol=annotations.benchmark_symbol,
        benchmark_price=annotations.benchmark_price,
        long_trend_value=annotations.long_trend_value,
        exit_line=annotations.exit_line,
        signal_date=annotations.signal_date,
        effective_date=annotations.effective_date,
        execution_timing_contract=annotations.execution_timing_contract,
        execution_calendar_source=annotations.execution_calendar_source,
        signal_effective_after_trading_days=annotations.signal_effective_after_trading_days,
        deploy_ratio_text=annotations.deploy_ratio_text,
        income_ratio_text=annotations.income_ratio_text,
        income_locked_ratio_text=annotations.income_locked_ratio_text,
        active_risk_asset=annotations.active_risk_asset,
        current_min_trade=current_min_trade,
        investable_cash=investable_cash,
    )


def map_strategy_decision_to_plan(
    decision: StrategyDecision,
    *,
    snapshot: Any,
    strategy_profile: str,
    runtime_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    canonical_profile = resolve_canonical_profile(strategy_profile)
    portfolio_inputs = build_value_target_portfolio_inputs_from_snapshot(
        snapshot,
        include_sellable_quantities=True,
        liquid_cash=float(snapshot.buying_power or snapshot.cash_balance or 0.0),
    )
    normalized_decision, translated_annotations = _normalize_to_value_decision(
        decision,
        portfolio_inputs=portfolio_inputs,
        runtime_metadata=runtime_metadata,
    )
    annotations = translated_annotations or _build_annotations(
        normalized_decision,
        portfolio_inputs=portfolio_inputs,
    )
    annotations = _apply_reserved_cash_policy(
        annotations,
        portfolio_inputs=portfolio_inputs,
        runtime_metadata=runtime_metadata,
    )
    plan = build_value_target_runtime_plan(
        normalized_decision,
        strategy_profile=canonical_profile,
        portfolio_inputs=portfolio_inputs,
        annotations=annotations,
        include_sellable_quantities=True,
    )
    metadata = getattr(snapshot, "metadata", {}) or {}
    cash_by_currency = metadata.get("cash_by_currency")
    if isinstance(cash_by_currency, Mapping):
        plan["portfolio"]["cash_by_currency"] = dict(cash_by_currency)
    return plan
