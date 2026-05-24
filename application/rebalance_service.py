"""Firstrade strategy execution orchestration.

The service follows the same boundary as the other platform repositories:
strategy logic stays in UsEquityStrategies, while this module assembles broker
inputs, maps strategy decisions to a value-target plan, and routes orders
through Firstrade-specific ports.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from application.execution_service import (
    execute_value_target_plan,
    substitute_small_safe_haven_targets_with_cash,
)
from application.firstrade_client import (
    FirstradeBrokerClient,
    FirstradeCredentials,
    mask_account_id,
)
from application.runtime_broker_adapters import build_runtime_broker_adapters
from application.state_persistence import GcsStateStore, build_gcs_state_store_from_env
from application.strategy_run_persistence import (
    build_strategy_run_state,
    is_duplicate_live_run,
    persist_strategy_run_state,
    read_latest_strategy_run_state,
    resolve_strategy_run_period,
)
from decision_mapper import map_strategy_decision_to_plan
from notifications.telegram import build_sender, build_translator, render_cycle_summary
from quant_platform_kit.common.runtime_inputs import (
    build_semiconductor_rotation_indicators_from_history,
    required_semiconductor_rotation_history_lookback,
)
from quant_platform_kit.strategy_contracts import build_strategy_evaluation_inputs
from runtime_config_support import PlatformRuntimeSettings, load_platform_runtime_settings
from strategy_runtime import load_strategy_runtime

LIMIT_SELL_DISCOUNT = 0.995
LIMIT_BUY_PREMIUM = 1.005
EXECUTION_BLOCKING_SKIP_REASONS = frozenset(
    {
        "buy_quantity_zero",
        "insufficient_cash_for_whole_share",
        "quote_unavailable",
        "sell_quantity_zero",
    }
)
TERMINAL_FUNDING_BLOCK_SKIP_REASONS = frozenset({"insufficient_cash_for_whole_share"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_project_id() -> str | None:
    return os.getenv("GOOGLE_CLOUD_PROJECT")


def _execution_blocking_skips(skipped_orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in skipped_orders
        if str(item.get("reason") or "") in EXECUTION_BLOCKING_SKIP_REASONS
    ]


def _is_terminal_funding_block(blocking_skips: list[dict[str, Any]]) -> bool:
    if not blocking_skips:
        return False
    return all(
        str(item.get("reason") or "") in TERMINAL_FUNDING_BLOCK_SKIP_REASONS
        for item in blocking_skips
    )


def _series_from_price_history(market_data_port, symbol: str) -> pd.Series:
    series = market_data_port.get_price_series(symbol)
    index = pd.DatetimeIndex([pd.Timestamp(point.as_of) for point in series.points])
    closes = [float(point.close) for point in series.points]
    return pd.Series(closes, index=index, dtype=float)


def _build_price_history(market_data_port, symbol: str) -> list[dict[str, float]]:
    series = market_data_port.get_price_series(symbol)
    return [
        {
            "close": float(point.close),
            "high": float(point.close),
            "low": float(point.close),
        }
        for point in series.points
    ]


def _build_market_history_loader(market_data_port):
    def load_market_history(_broker_client, symbol, *_args, **_kwargs):
        return _series_from_price_history(market_data_port, str(symbol).strip().upper())

    return load_market_history


def _build_derived_indicators(market_data_port, *, trend_ma_window: int):
    lookback = required_semiconductor_rotation_history_lookback(trend_ma_window=trend_ma_window)
    soxl = _series_from_price_history(market_data_port, "SOXL").tail(lookback)
    soxx = _series_from_price_history(market_data_port, "SOXX").tail(lookback)
    return build_semiconductor_rotation_indicators_from_history(
        soxl_history=soxl,
        soxx_history=soxx,
        trend_ma_window=trend_ma_window,
    )


def build_market_inputs(
    *,
    available_inputs: set[str],
    market_data_port,
    benchmark_symbol: str,
    strategy_runtime_config: Mapping[str, Any],
) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    if "market_history" in available_inputs:
        inputs["market_history"] = _build_market_history_loader(market_data_port)
    if "benchmark_history" in available_inputs:
        inputs["benchmark_history"] = _build_price_history(market_data_port, benchmark_symbol)
    if "qqq_history" in available_inputs:
        inputs["qqq_history"] = _build_price_history(market_data_port, benchmark_symbol)
    if "derived_indicators" in available_inputs or "indicators" in available_inputs:
        indicators = _build_derived_indicators(
            market_data_port,
            trend_ma_window=int(strategy_runtime_config.get("trend_ma_window", 150)),
        )
        if "derived_indicators" in available_inputs:
            inputs["derived_indicators"] = indicators
        if "indicators" in available_inputs:
            inputs["indicators"] = indicators
    return inputs


def _connect_client(
    *,
    credentials: FirstradeCredentials,
    live_trading_enabled: bool,
    client_factory: Callable[..., FirstradeBrokerClient] = FirstradeBrokerClient,
) -> FirstradeBrokerClient:
    return client_factory(
        credentials,
        live_trading_enabled=live_trading_enabled,
    ).connect()


def _publish_cycle_notification(
    result: Mapping[str, Any],
    *,
    settings: PlatformRuntimeSettings,
    notification_sender: Callable[[str], None] | None = None,
) -> bool:
    sender = notification_sender
    if sender is None:
        if not settings.tg_token or not settings.tg_chat_id:
            return False
        sender = build_sender(settings.tg_token, settings.tg_chat_id)
    sender(render_cycle_summary(result, lang=settings.notify_lang))
    return True


def run_strategy_cycle(
    *,
    runtime_settings: PlatformRuntimeSettings | None = None,
    credentials: FirstradeCredentials | None = None,
    client_factory: Callable[..., FirstradeBrokerClient] = FirstradeBrokerClient,
    state_store: GcsStateStore | None = None,
    notification_sender: Callable[[str], None] | None = None,
    env_reader: Callable[[str, str | None], str | None] = os.getenv,
) -> dict[str, Any]:
    now = _utcnow()
    settings = runtime_settings or load_platform_runtime_settings(project_id_resolver=get_project_id)
    resolved_credentials = credentials or FirstradeCredentials.from_env(env_reader)
    store = state_store or build_gcs_state_store_from_env(env_reader)
    persist_strategy_runs = bool(settings.persist_strategy_runs and store is not None)
    client = _connect_client(
        credentials=resolved_credentials,
        live_trading_enabled=settings.live_trading_enabled,
        client_factory=client_factory,
    )
    print(f"Firstrade session reused={bool(getattr(client, 'session_reused', False))}", flush=True)
    account = client.select_account(env_reader("FIRSTRADE_ACCOUNT", "") or None)
    strategy_runtime = load_strategy_runtime(
        settings.strategy_profile,
        runtime_settings=settings,
        logger=lambda message: print(message, flush=True),
    )
    broker_adapters = build_runtime_broker_adapters(
        client=client,
        account=account,
        strategy_symbols=tuple(strategy_runtime.managed_symbols),
        account_hash=mask_account_id(account),
        live_orders=not settings.dry_run_only,
        live_order_ack=settings.live_order_ack,
        max_order_notional_usd=settings.max_order_notional_usd,
    )
    market_data_port = broker_adapters.build_market_data_port()
    portfolio_port = broker_adapters.build_portfolio_port()
    execution_port = broker_adapters.build_execution_port()
    snapshot = portfolio_port.get_portfolio_snapshot()

    available_inputs = set(strategy_runtime.runtime_adapter.available_inputs)
    benchmark_symbol = str(strategy_runtime.merged_runtime_config.get("benchmark_symbol", "QQQ"))
    market_inputs = build_market_inputs(
        available_inputs=available_inputs,
        market_data_port=market_data_port,
        benchmark_symbol=benchmark_symbol,
        strategy_runtime_config=strategy_runtime.merged_runtime_config,
    )
    evaluation_inputs = build_strategy_evaluation_inputs(
        available_inputs=available_inputs,
        market_inputs=market_inputs,
        portfolio_snapshot=snapshot,
        translator=build_translator(settings.notify_lang),
    )
    evaluation = strategy_runtime.evaluate(**evaluation_inputs)
    plan = map_strategy_decision_to_plan(
        evaluation.decision,
        snapshot=snapshot,
        strategy_profile=settings.strategy_profile,
        runtime_metadata=getattr(evaluation, "metadata", None),
    )
    plan = substitute_small_safe_haven_targets_with_cash(
        plan,
        threshold_usd=settings.safe_haven_cash_substitute_threshold_usd,
    )
    run_period = resolve_strategy_run_period(
        now=now,
        plan=plan,
        evaluation_metadata=getattr(evaluation, "metadata", None),
    )
    masked_account = mask_account_id(account)
    existing_run = None
    if persist_strategy_runs and not settings.dry_run_only:
        existing_run = read_latest_strategy_run_state(
            store=store,
            account=masked_account,
            strategy_profile=strategy_runtime.profile,
            run_period=run_period,
        )
        if is_duplicate_live_run(existing_run):
            return {
                "ok": True,
                "api_kind": "unofficial-reverse-engineered",
                "account": masked_account,
                "strategy_profile": strategy_runtime.profile,
                "strategy_display_name": strategy_runtime.display_name,
                "dry_run_only": settings.dry_run_only,
                "live_trading_enabled": settings.live_trading_enabled,
                "session_reused": bool(getattr(client, "session_reused", False)),
                "strategy_run_period": run_period,
                "strategy_run_persisted": False,
                "idempotency_skipped": True,
                "existing_strategy_run_stage": existing_run.get("stage"),
                "existing_strategy_run_as_of": existing_run.get("as_of"),
                "submitted_orders": [],
                "skipped_orders": [
                    {
                        "reason": "duplicate_live_strategy_run",
                        "run_period": run_period,
                    }
                ],
                "action_done": False,
            }
    strategy_run_persisted = False
    strategy_run_persistence_error = None
    if persist_strategy_runs:
        planned_state = build_strategy_run_state(
            stage="ORDERS_PLANNED",
            account=masked_account,
            strategy_profile=strategy_runtime.profile,
            strategy_display_name=strategy_runtime.display_name,
            run_period=run_period,
            dry_run_only=settings.dry_run_only,
            live_trading_enabled=settings.live_trading_enabled,
            session_reused=bool(getattr(client, "session_reused", False)),
            portfolio_snapshot=plan.get("portfolio", {}),
            evaluation_metadata=getattr(evaluation, "metadata", None),
            plan=plan,
            now=now,
        )
        try:
            strategy_run_persisted = persist_strategy_run_state(
                store=store,
                state=planned_state,
                now=now,
            )
        except Exception as exc:
            strategy_run_persisted = False
            strategy_run_persistence_error = f"{type(exc).__name__}: {exc}"
    execution_result = execute_value_target_plan(
        plan=plan,
        market_data_port=market_data_port,
        execution_port=execution_port,
        dry_run_only=settings.dry_run_only,
        limit_sell_discount=LIMIT_SELL_DISCOUNT,
        limit_buy_premium=LIMIT_BUY_PREMIUM,
        max_order_notional_usd=settings.max_order_notional_usd,
        safe_haven_cash_substitute_threshold_usd=settings.safe_haven_cash_substitute_threshold_usd,
    )
    submitted_orders = list(execution_result.submitted_orders)
    skipped_orders = list(execution_result.skipped_orders)
    blocking_skips = _execution_blocking_skips(skipped_orders)
    execution_blocked = bool(blocking_skips)
    funding_blocked = _is_terminal_funding_block(blocking_skips)
    terminal_funding_block = funding_blocked and not execution_result.action_done
    result = {
        "ok": not execution_blocked,
        "api_kind": "unofficial-reverse-engineered",
        "account": mask_account_id(account),
        "strategy_profile": strategy_runtime.profile,
        "strategy_display_name": strategy_runtime.display_name,
        "dry_run_only": settings.dry_run_only,
        "live_trading_enabled": settings.live_trading_enabled,
        "session_reused": bool(getattr(client, "session_reused", False)),
        "strategy_run_period": run_period,
        "strategy_run_persisted": strategy_run_persisted,
        "portfolio": plan.get("portfolio", {}),
        "allocation": plan.get("allocation", {}),
        "execution": plan.get("execution", {}),
        "submitted_orders": submitted_orders,
        "skipped_orders": skipped_orders,
        "action_done": execution_result.action_done,
    }
    if execution_blocked:
        result["execution_blocked"] = True
        result["execution_block_retryable"] = not terminal_funding_block
        result["execution_blocking_skips"] = blocking_skips
        result["error"] = "Strategy execution blocked; see execution_blocking_skips."
    if funding_blocked:
        result["funding_blocked"] = True
    if strategy_run_persistence_error:
        result["strategy_run_persistence_error"] = strategy_run_persistence_error
    if persist_strategy_runs:
        stage = "DRY_RUN_COMPLETED"
        if not settings.dry_run_only:
            if terminal_funding_block and not execution_result.action_done:
                stage = "FUNDING_BLOCKED"
            elif execution_blocked and execution_result.action_done:
                stage = "PARTIAL_SUBMITTED"
            elif execution_blocked:
                stage = "EXECUTION_BLOCKED"
            else:
                stage = "SUBMITTED" if execution_result.action_done else "NO_ACTION"
        completed_state = build_strategy_run_state(
            stage=stage,
            account=masked_account,
            strategy_profile=strategy_runtime.profile,
            strategy_display_name=strategy_runtime.display_name,
            run_period=run_period,
            dry_run_only=settings.dry_run_only,
            live_trading_enabled=settings.live_trading_enabled,
            session_reused=bool(getattr(client, "session_reused", False)),
            portfolio_snapshot=plan.get("portfolio", {}),
            evaluation_metadata=getattr(evaluation, "metadata", None),
            plan=plan,
            submitted_orders=list(execution_result.submitted_orders),
            skipped_orders=list(execution_result.skipped_orders),
            action_done=execution_result.action_done,
            now=now,
        )
        try:
            result["strategy_run_persisted"] = persist_strategy_run_state(
                store=store,
                state=completed_state,
                now=now,
            )
        except Exception as exc:
            result["strategy_run_persisted"] = False
            result["strategy_run_persistence_error"] = f"{type(exc).__name__}: {exc}"
    try:
        result["notification_sent"] = _publish_cycle_notification(
            result,
            settings=settings,
            notification_sender=notification_sender,
        )
    except Exception as exc:
        result["notification_sent"] = False
        result["notification_error"] = f"{type(exc).__name__}: {exc}"
    return result
