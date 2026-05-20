"""Firstrade strategy execution orchestration.

The service follows the same boundary as the other platform repositories:
strategy logic stays in UsEquityStrategies, while this module assembles broker
inputs, maps strategy decisions to a value-target plan, and routes orders
through Firstrade-specific ports.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

import pandas as pd

from application.execution_service import execute_value_target_plan
from application.firstrade_client import (
    FirstradeBrokerClient,
    FirstradeCredentials,
    mask_account_id,
)
from application.runtime_broker_adapters import build_runtime_broker_adapters
from decision_mapper import map_strategy_decision_to_plan
from notifications.telegram import build_sender, render_cycle_summary
from quant_platform_kit.common.runtime_inputs import (
    build_semiconductor_rotation_indicators_from_history,
    required_semiconductor_rotation_history_lookback,
)
from quant_platform_kit.strategy_contracts import build_strategy_evaluation_inputs
from runtime_config_support import PlatformRuntimeSettings, load_platform_runtime_settings
from strategy_runtime import load_strategy_runtime

LIMIT_SELL_DISCOUNT = 0.995
LIMIT_BUY_PREMIUM = 1.005


def get_project_id() -> str | None:
    return os.getenv("GOOGLE_CLOUD_PROJECT")


def _identity_translator(key: str, **kwargs) -> str:
    if not kwargs:
        return key
    details = ", ".join(f"{name}={value}" for name, value in sorted(kwargs.items()))
    return f"{key}: {details}"


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
    notification_sender: Callable[[str], None] | None = None,
    env_reader: Callable[[str, str | None], str | None] = os.getenv,
) -> dict[str, Any]:
    settings = runtime_settings or load_platform_runtime_settings(project_id_resolver=get_project_id)
    resolved_credentials = credentials or FirstradeCredentials.from_env(env_reader)
    client = _connect_client(
        credentials=resolved_credentials,
        live_trading_enabled=settings.live_trading_enabled,
        client_factory=client_factory,
    )
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
        translator=_identity_translator,
    )
    evaluation = strategy_runtime.evaluate(**evaluation_inputs)
    plan = map_strategy_decision_to_plan(
        evaluation.decision,
        snapshot=snapshot,
        strategy_profile=settings.strategy_profile,
        runtime_metadata=getattr(evaluation, "metadata", None),
    )
    execution_result = execute_value_target_plan(
        plan=plan,
        market_data_port=market_data_port,
        execution_port=execution_port,
        dry_run_only=settings.dry_run_only,
        limit_sell_discount=LIMIT_SELL_DISCOUNT,
        limit_buy_premium=LIMIT_BUY_PREMIUM,
        max_order_notional_usd=settings.max_order_notional_usd,
    )
    result = {
        "ok": True,
        "api_kind": "unofficial-reverse-engineered",
        "account": mask_account_id(account),
        "strategy_profile": strategy_runtime.profile,
        "strategy_display_name": strategy_runtime.display_name,
        "dry_run_only": settings.dry_run_only,
        "live_trading_enabled": settings.live_trading_enabled,
        "portfolio": plan.get("portfolio", {}),
        "allocation": plan.get("allocation", {}),
        "execution": plan.get("execution", {}),
        "submitted_orders": list(execution_result.submitted_orders),
        "skipped_orders": list(execution_result.skipped_orders),
        "action_done": execution_result.action_done,
    }
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
