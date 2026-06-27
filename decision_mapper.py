from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from us_equity_strategies.cash_only_equity import (
    build_portfolio_inputs_from_snapshot,
    resolve_weight_translation_equity,
)
from quant_platform_kit.strategy_contracts import (
    PositionTarget,
    StrategyContractValidationError,
    StrategyDecision,
    ValueTargetExecutionAnnotations,
    build_value_target_execution_annotations,
    build_value_target_runtime_plan,
    resolve_decision_target_mode,
    translate_decision_to_target_mode,
)
from us_equity_strategies.catalog import resolve_canonical_profile

_SAFE_HAVEN_SYMBOLS = frozenset({"BOXX", "BIL"})
_INCOME_SYMBOLS = frozenset({"QQQI", "SPYI"})
_DEFAULT_MIN_TRADE_FLOOR = 100.0
_DEFAULT_REBALANCE_THRESHOLD_RATIO = 0.01
_TQQQ_RISK_CONTROL_EXECUTION_FIELDS = (
    "dual_drive_volatility_delever_enabled",
    "dual_drive_volatility_delever_window",
    "dual_drive_volatility_delever_threshold_mode",
    "dual_drive_volatility_delever_threshold",
    "dual_drive_volatility_delever_exit_threshold",
    "dual_drive_volatility_delever_dynamic_threshold",
    "dual_drive_volatility_delever_dynamic_sample_count",
    "dual_drive_volatility_delever_dynamic_lookback",
    "dual_drive_volatility_delever_dynamic_percentile",
    "dual_drive_volatility_delever_dynamic_min_periods",
    "dual_drive_volatility_delever_dynamic_floor",
    "dual_drive_volatility_delever_dynamic_cap",
    "dual_drive_volatility_delever_metric",
    "dual_drive_volatility_delever_triggered",
    "dual_drive_volatility_delever_entry_triggered",
    "dual_drive_volatility_delever_hysteresis_triggered",
    "dual_drive_volatility_delever_trigger_reason",
    "dual_drive_volatility_delever_applied",
    "dual_drive_volatility_delever_vetoed",
    "dual_drive_volatility_delever_veto_reason",
    "dual_drive_volatility_delever_taco_veto_enabled",
    "dual_drive_volatility_delever_taco_rebound_context_active",
    "dual_drive_volatility_delever_true_crisis_active",
    "dual_drive_volatility_delever_retention_mode",
    "dual_drive_volatility_delever_retention_policy",
    "dual_drive_volatility_delever_retention_ratio",
    "dual_drive_volatility_delever_retention_source",
    "dual_drive_volatility_delever_retention_context_found",
    "dual_drive_volatility_delever_retention_reason_codes",
    "dual_drive_volatility_delever_redirect_symbol",
    "dual_drive_volatility_delever_source_value",
    "dual_drive_volatility_delever_retained_value",
    "dual_drive_volatility_delever_removed_value",
    "dual_drive_volatility_delever_retained_ratio",
    "dual_drive_volatility_delever_redirected_ratio",
    "dual_drive_macro_risk_governor_enabled",
    "dual_drive_macro_risk_governor_found",
    "dual_drive_macro_risk_governor_route",
    "dual_drive_macro_risk_governor_active",
    "dual_drive_macro_risk_governor_applied",
    "dual_drive_macro_risk_governor_leverage_scalar",
    "dual_drive_macro_risk_governor_risk_asset_scalar",
    "dual_drive_macro_risk_governor_removed_value",
    "dual_drive_macro_risk_governor_redirected_to_unlevered",
    "dual_drive_crisis_defense_enabled",
    "dual_drive_crisis_defense_triggered",
    "dual_drive_crisis_defense_applied",
    "dual_drive_crisis_defense_destination",
    "dual_drive_crisis_defense_removed_value",
)
_SOXL_RISK_CONTROL_EXECUTION_FIELDS = (
    "blend_gate_volatility_delever_enabled",
    "blend_gate_volatility_delever_symbol",
    "blend_gate_volatility_delever_window",
    "blend_gate_volatility_delever_threshold_mode",
    "blend_gate_volatility_delever_threshold",
    "blend_gate_volatility_delever_dynamic_threshold",
    "blend_gate_volatility_delever_dynamic_sample_count",
    "blend_gate_volatility_delever_dynamic_lookback",
    "blend_gate_volatility_delever_dynamic_percentile",
    "blend_gate_volatility_delever_dynamic_min_periods",
    "blend_gate_volatility_delever_dynamic_floor",
    "blend_gate_volatility_delever_dynamic_cap",
    "blend_gate_volatility_delever_metric",
    "blend_gate_volatility_delever_triggered",
    "blend_gate_volatility_delever_retention_ratio",
    "blend_gate_volatility_delever_retention_mode",
    "blend_gate_volatility_delever_retention_policy",
    "blend_gate_volatility_delever_effective_retention_ratio",
    "blend_gate_volatility_delever_retention_source",
    "blend_gate_volatility_delever_retention_context_found",
    "blend_gate_volatility_delever_retention_reason_codes",
    "blend_gate_volatility_delever_redirect_symbol",
    "blend_gate_volatility_delever_removed_ratio",
)
_MARKET_REGIME_CONTROL_EXECUTION_FIELDS = (
    "market_regime_control_enabled",
    "market_regime_control_found",
    "market_regime_control_source",
    "market_regime_control_schema_version",
    "market_regime_control_route",
    "market_regime_control_route_source",
    "market_regime_control_active",
    "market_regime_control_applied",
    "market_regime_control_route_allowed",
    "market_regime_control_risk_scalar",
    "market_regime_control_risk_budget_scalar",
    "market_regime_control_leverage_scalar",
    "market_regime_control_risk_asset_scalar",
    "market_regime_control_taco_allowed",
    "market_regime_control_local_delever_veto_allowed",
    "market_regime_control_crisis_defense_required",
    "market_regime_control_blocked_actions",
    "market_regime_control_vetoes",
    "market_regime_control_reason_codes",
    "market_regime_control_removed_weight",
    "market_regime_control_removed_ratio",
    "market_regime_control_redirected_to_unlevered_ratio",
    "market_regime_control_safe_haven",
    "market_regime_control_risk_symbols",
)


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


def _build_zero_equity_no_execute_decision(
    decision: StrategyDecision,
    *,
    portfolio_inputs,
    diagnostics: Mapping[str, Any],
) -> StrategyDecision:
    if portfolio_inputs.market_values:
        return _build_hold_current_value_decision(portfolio_inputs, diagnostics=diagnostics)
    positions = []
    for position in decision.positions:
        positions.append(
            PositionTarget(
                symbol=position.symbol,
                target_value=0.0,
                role=position.role or _symbol_role(position.symbol),
                order_preference=position.order_preference,
            )
        )
    return StrategyDecision(
        positions=tuple(positions),
        risk_flags=tuple(dict.fromkeys((*decision.risk_flags, "no_execute"))),
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
    cash_only_execution: bool = True,
) -> tuple[StrategyDecision, ValueTargetExecutionAnnotations | None]:
    target_mode = resolve_decision_target_mode(decision)
    no_execute = "no_execute" in set(decision.risk_flags)
    if target_mode == "value" and not no_execute:
        return decision, None
    if target_mode == "weight" and not no_execute:
        total_equity, block_execution, deleverage_mode = resolve_weight_translation_equity(
            portfolio_inputs,
            cash_only_execution=cash_only_execution,
        )
        if block_execution:
            diagnostics = {
                **dict(runtime_metadata or {}),
                **dict(decision.diagnostics),
                "execution_blocked_reason": "non_positive_total_equity",
                "portfolio_total_equity": float(portfolio_inputs.total_equity),
            }
            return _build_zero_equity_no_execute_decision(
                decision,
                portfolio_inputs=portfolio_inputs,
                diagnostics=diagnostics,
            ), _build_weight_translation_annotations(
                decision,
                total_equity=float(portfolio_inputs.total_equity),
                liquid_cash=float(portfolio_inputs.liquid_cash),
                runtime_metadata=runtime_metadata,
            )
        diagnostics = dict(decision.diagnostics)
        if deleverage_mode:
            diagnostics["cash_only_deleverage_mode"] = True
        translated = translate_decision_to_target_mode(
            replace(decision, diagnostics=diagnostics) if deleverage_mode else decision,
            target_mode="value",
            total_equity=total_equity,
        )
        return translated, _build_weight_translation_annotations(
            decision,
            total_equity=total_equity,
            liquid_cash=float(portfolio_inputs.liquid_cash),
            runtime_metadata=runtime_metadata,
        )
    diagnostics = {**dict(runtime_metadata or {}), **dict(decision.diagnostics)}
    return _build_hold_current_value_decision(portfolio_inputs, diagnostics=diagnostics), None


def _build_annotations(decision: StrategyDecision, *, portfolio_inputs) -> ValueTargetExecutionAnnotations:
    try:
        annotations = build_value_target_execution_annotations(decision)
    except StrategyContractValidationError as exc:
        if "requires trade_threshold_value" not in str(exc):
            raise
        diagnostics = dict(decision.diagnostics)
        raw_annotations = diagnostics.get("execution_annotations")
        execution_annotations = (
            dict(raw_annotations) if isinstance(raw_annotations, Mapping) else {}
        )
        execution_annotations["trade_threshold_value"] = _default_threshold_value(
            float(portfolio_inputs.total_equity)
        )
        diagnostics["execution_annotations"] = execution_annotations
        annotations = build_value_target_execution_annotations(
            replace(decision, diagnostics=diagnostics)
        )
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
    raw_policy = (runtime_metadata or {}).get("firstrade_execution_policy")
    cash_only_execution = True
    if isinstance(raw_policy, Mapping):
        cash_only_execution = bool(raw_policy.get("cash_only_execution", True))
    portfolio_inputs = build_portfolio_inputs_from_snapshot(
        snapshot,
        cash_only_execution=cash_only_execution,
        include_sellable_quantities=True,
    )
    normalized_decision, translated_annotations = _normalize_to_value_decision(
        decision,
        portfolio_inputs=portfolio_inputs,
        runtime_metadata=runtime_metadata,
        cash_only_execution=cash_only_execution,
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
    diagnostics = {
        **dict(runtime_metadata or {}),
        **dict(decision.diagnostics),
        **dict(normalized_decision.diagnostics),
    }
    for source in (
        (runtime_metadata or {}).get("execution_annotations"),
        decision.diagnostics.get("execution_annotations"),
        normalized_decision.diagnostics.get("execution_annotations"),
    ):
        if isinstance(source, Mapping):
            diagnostics.update(source)
    execution = plan.setdefault("execution", {})
    for field_name in (
        *_MARKET_REGIME_CONTROL_EXECUTION_FIELDS,
        *_TQQQ_RISK_CONTROL_EXECUTION_FIELDS,
        *_SOXL_RISK_CONTROL_EXECUTION_FIELDS,
    ):
        if field_name in diagnostics:
            execution[field_name] = diagnostics[field_name]
    return plan
