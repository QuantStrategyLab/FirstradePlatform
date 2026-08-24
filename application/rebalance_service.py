"""Firstrade strategy execution orchestration.

The service follows the same boundary as the other platform repositories:
strategy logic stays in UsEquityStrategies, while this module assembles broker
inputs, maps strategy decisions to a value-target plan, and routes orders
through Firstrade-specific ports.
"""

from __future__ import annotations

import json
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
from application.signal_snapshot import build_signal_snapshot
from application.state_persistence import GcsStateStore, build_gcs_state_store_from_env
from application.strategy_run_persistence import (
    build_strategy_run_state,
    claim_live_strategy_run,
    is_duplicate_live_run,
    persist_strategy_run_state,
    read_latest_strategy_run_state,
    resolve_strategy_run_period,
)
from decision_mapper import map_strategy_decision_to_plan
from notifications.telegram import build_sender, build_translator, render_cycle_summary
from quant_platform_kit.common.execution_outcomes import (
    DEFAULT_EXECUTION_BLOCKING_SKIP_REASONS,
    filter_execution_blocking_skips,
    is_terminal_funding_block,
    resolve_strategy_run_stage,
)
from quant_platform_kit.common.runtime_inputs import (
    build_semiconductor_rotation_indicators_from_history,
    required_semiconductor_rotation_history_lookback,
)
from quant_platform_kit.common.strategy_plugins import (
    attach_strategy_plugin_metadata,
    build_strategy_plugin_error_notification_lines,
    build_strategy_plugin_report_payload,
    load_configured_strategy_plugin_signals,
    parse_strategy_plugin_mounts,
)
from quant_platform_kit.notifications.events import NotificationPublisher, RenderedNotification
from quant_platform_kit.notifications.strategy_plugin_alerts import (
    StrategyPluginAlertStateSettings,
    build_strategy_plugin_alert_context_label as build_alert_context_label,
    publish_strategy_plugin_alerts as dispatch_strategy_plugin_alerts,
)
from quant_platform_kit.strategy_contracts import build_strategy_evaluation_inputs
from quant_platform_kit.strategy_lifecycle.performance_monitor import try_record_platform_execution
from runtime_config_support import IBIT_SMART_DCA_PROFILE, PlatformRuntimeSettings, load_platform_runtime_settings
from runtime_execution_policy import dca_execution_unsupported_reason, notional_buy_execution_enabled
from us_equity_strategies.signals import resolve_external_market_signal_inputs
from strategy_runtime import load_strategy_runtime

LIMIT_SELL_DISCOUNT = 0.995
LIMIT_BUY_PREMIUM = 1.005
DEFAULT_LIMIT_BUY_PREMIUM_BY_SYMBOL = {"SOXL": 1.015, "TQQQ": 1.010}
BROKER_EXECUTION_BLOCKING_SKIP_REASONS = frozenset(
    {
        *DEFAULT_EXECUTION_BLOCKING_SKIP_REASONS,
        "broker_rejected",
        "fractional_trading_disclosure_required",
    }
)


def _load_limit_buy_premium_by_symbol(*env_names: str) -> dict[str, float]:
    raw_value = ""
    for env_name in env_names:
        value = os.getenv(env_name)
        if value and value.strip():
            raw_value = value.strip()
            break
    if not raw_value:
        return dict(DEFAULT_LIMIT_BUY_PREMIUM_BY_SYMBOL)
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid limit buy premium map JSON: {raw_value!r}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Limit buy premium map must be a JSON object keyed by symbol.")
    parsed: dict[str, float] = {}
    for symbol, premium in payload.items():
        symbol_text = str(symbol or "").strip().upper()
        if not symbol_text:
            continue
        premium_value = float(premium)
        if premium_value <= 0.0:
            raise ValueError(f"Limit buy premium for {symbol_text} must be positive.")
        parsed[symbol_text] = premium_value
    return parsed


LIMIT_BUY_PREMIUM_BY_SYMBOL = _load_limit_buy_premium_by_symbol(
    "FIRSTRADE_LIMIT_BUY_PREMIUM_BY_SYMBOL_JSON",
    "LIMIT_BUY_PREMIUM_BY_SYMBOL_JSON",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_project_id() -> str | None:
    return os.getenv("GOOGLE_CLOUD_PROJECT")


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


def _ibit_smart_multiplier_enabled(
    strategy_profile: str | None,
    strategy_runtime_config: Mapping[str, Any],
) -> bool:
    if str(strategy_profile or "").strip().lower() != IBIT_SMART_DCA_PROFILE:
        return False
    return bool(strategy_runtime_config.get("smart_multiplier_enabled"))


def build_market_inputs(
    *,
    available_inputs: set[str],
    market_data_port,
    benchmark_symbol: str,
    strategy_runtime_config: Mapping[str, Any],
    strategy_profile: str | None = None,
    runtime_settings: PlatformRuntimeSettings | None = None,
    log_message: Callable[[str], None] = print,
) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    if runtime_settings is not None and strategy_profile is not None:
        inputs.update(
            resolve_external_market_signal_inputs(
                strategy_profile=strategy_profile,
                available_inputs=available_inputs,
                runtime_settings=runtime_settings,
                logger=log_message,
            )
        )
    skip_broker_market_history = _ibit_smart_multiplier_enabled(
        strategy_profile,
        strategy_runtime_config,
    )
    if "market_history" in available_inputs and not skip_broker_market_history:
        inputs["market_history"] = _build_market_history_loader(market_data_port)
    if "benchmark_history" in available_inputs:
        inputs["benchmark_history"] = _build_price_history(market_data_port, benchmark_symbol)
    if "qqq_history" in available_inputs:
        inputs["qqq_history"] = _build_price_history(market_data_port, benchmark_symbol)
    if (
        ("derived_indicators" in available_inputs and "derived_indicators" not in inputs)
        or ("indicators" in available_inputs and "indicators" not in inputs)
    ):
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
    log_message: Callable[[str], None] = print,
) -> bool:
    sender = notification_sender
    if sender is None:
        if not settings.tg_token or not settings.tg_chat_id:
            return False
        sender = build_sender(settings.tg_token, settings.tg_chat_id)
    message = render_cycle_summary(result, lang=settings.notify_lang)
    def publish_log(text: str) -> None:
        try:
            log_message(text, flush=True)
        except TypeError:
            log_message(text)

    delivery_sent = True

    def send_and_capture(text: str) -> bool | None:
        nonlocal delivery_sent
        outcome = sender(text)
        delivery_sent = outcome is not False
        return outcome

    NotificationPublisher(
        log_message=publish_log,
        send_message=send_and_capture,
    ).publish(RenderedNotification(detailed_text=message, compact_text=message))
    return delivery_sent


def _should_publish_cycle_notification(result: Mapping[str, Any]) -> bool:
    if result.get("submitted_orders"):
        return True
    if result.get("error") or result.get("ok") is False:
        return True
    return False


def load_strategy_plugin_signals(
    raw_mounts,
    *,
    strategy_profile: str,
    parse_mounts_fn=parse_strategy_plugin_mounts,
    load_signals_fn=load_configured_strategy_plugin_signals,
):
    if not raw_mounts:
        return (), None
    try:
        mounts = parse_mounts_fn(raw_mounts)
        if not mounts:
            return (), None
        return load_signals_fn(mounts, strategy_profile=strategy_profile), None
    except Exception as exc:
        return (), f"{type(exc).__name__}: {exc}"


def attach_strategy_plugin_result(
    result: dict[str, Any],
    *,
    signals,
    error: str | None,
    translator: Callable[..., str],
) -> dict[str, Any]:
    if signals:
        result.update(build_strategy_plugin_report_payload(signals))
    if error:
        result["strategy_plugin_error"] = error
        result["strategy_plugin_error_lines"] = build_strategy_plugin_error_notification_lines(
            error,
            translator=translator,
        )
    return result


def build_strategy_plugin_alert_context_label(settings: PlatformRuntimeSettings) -> str:
    return build_alert_context_label(
        platform_id="firstrade",
        strategy_profile=settings.strategy_profile,
        account_scope=settings.account_region or settings.account_prefix,
        service_name=settings.account_prefix,
        runtime_target=settings.runtime_target,
    )


def build_strategy_plugin_alert_state_settings(
    settings: PlatformRuntimeSettings,
    *,
    env_reader: Callable[[str, str | None], str | None] = os.getenv,
):
    state_bucket = env_reader("FIRSTRADE_GCS_STATE_BUCKET", None)
    state_prefix = env_reader("FIRSTRADE_STATE_PREFIX", "firstrade-platform") or "firstrade-platform"
    state_gcs_uri = f"gs://{state_bucket}/{state_prefix}" if state_bucket else None
    return StrategyPluginAlertStateSettings.from_env(
        env_reader=env_reader,
        project_id=settings.project_id,
        fallback_cloud_prefix_uri=state_gcs_uri,
    )


def publish_strategy_plugin_alerts(
    signals,
    *,
    settings: PlatformRuntimeSettings,
    translator: Callable[..., str],
    log_message: Callable[..., Any] = print,
    env_reader: Callable[[str, str | None], str | None] = os.getenv,
):
    return dispatch_strategy_plugin_alerts(
        signals,
        notification_settings=settings,
        translator=translator,
        strategy_label=settings.strategy_profile,
        context_label=build_strategy_plugin_alert_context_label(settings),
        state_settings=build_strategy_plugin_alert_state_settings(settings, env_reader=env_reader),
        log_message=log_message,
    )


def empty_strategy_plugin_alert_report_fields() -> dict[str, Any]:
    return {
        "strategy_plugin_alert_attempted_count": 0,
        "strategy_plugin_alert_sent_count": 0,
        "strategy_plugin_alert_skipped_count": 0,
        "strategy_plugin_alert_failed_count": 0,
        "strategy_plugin_alert_email_attempted_count": 0,
        "strategy_plugin_alert_email_sent_count": 0,
        "strategy_plugin_alert_email_skipped_count": 0,
        "strategy_plugin_alert_email_failed_count": 0,
        "strategy_plugin_alert_email_deliveries": [],
        "strategy_plugin_alert_sms_attempted_count": 0,
        "strategy_plugin_alert_sms_sent_count": 0,
        "strategy_plugin_alert_sms_skipped_count": 0,
        "strategy_plugin_alert_sms_failed_count": 0,
        "strategy_plugin_alert_sms_deliveries": [],
    }


def _runtime_metadata_with_execution_policy(
    metadata: Mapping[str, Any] | None,
    *,
    settings: PlatformRuntimeSettings,
) -> dict[str, Any]:
    runtime_metadata = dict(metadata or {})
    runtime_metadata["firstrade_execution_policy"] = {
        "reserved_cash_floor_usd": float(settings.reserved_cash_floor_usd or 0.0),
        "reserved_cash_ratio": float(settings.reserved_cash_ratio or 0.0),
        "cash_only_execution": bool(settings.cash_only_execution),
    }
    return runtime_metadata


def _unsupported_strategy_execution_result(
    *,
    settings: PlatformRuntimeSettings,
    skip_reason: str,
    strategy_plugin_signals,
    strategy_plugin_error: str | None,
    translator: Callable[..., str],
) -> dict[str, Any]:
    skipped_orders = [{"reason": skip_reason, "strategy_profile": settings.strategy_profile}]
    result = {
        "ok": True,
        "status": "skipped",
        "skip_reason": skip_reason,
        "api_kind": "unofficial-reverse-engineered",
        "strategy_profile": settings.strategy_profile,
        "strategy_display_name": settings.strategy_display_name,
        "dry_run_only": settings.dry_run_only,
        "live_trading_enabled": settings.live_trading_enabled,
        "submitted_orders": [],
        "skipped_orders": skipped_orders,
        "action_done": False,
        "strategy_run_stage": "NO_ACTION",
        **empty_strategy_plugin_alert_report_fields(),
    }
    return attach_strategy_plugin_result(
        result,
        signals=strategy_plugin_signals,
        error=strategy_plugin_error,
        translator=translator,
    )


def run_strategy_cycle(
    *,
    runtime_settings: PlatformRuntimeSettings | None = None,
    credentials: FirstradeCredentials | None = None,
    client_factory: Callable[..., FirstradeBrokerClient] = FirstradeBrokerClient,
    state_store: GcsStateStore | None = None,
    notification_sender: Callable[[str], None] | None = None,
    env_reader: Callable[[str, str | None], str | None] = os.getenv,
    send_cycle_notification: bool = True,
    dispatch_plugin_alerts: bool = True,
) -> dict[str, Any]:
    now = _utcnow()
    settings = runtime_settings or load_platform_runtime_settings(project_id_resolver=get_project_id)
    translator = build_translator(settings.notify_lang)

    def log_message(message: str) -> None:
        print(message, flush=True)

    strategy_plugin_signals, strategy_plugin_error = load_strategy_plugin_signals(
        settings.strategy_plugin_mounts_json,
        strategy_profile=settings.strategy_profile,
    )
    unsupported_reason = dca_execution_unsupported_reason(settings.strategy_profile)
    if unsupported_reason is not None:
        return _unsupported_strategy_execution_result(
            settings=settings,
            skip_reason=unsupported_reason,
            strategy_plugin_signals=strategy_plugin_signals,
            strategy_plugin_error=strategy_plugin_error,
            translator=translator,
        )
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
        logger=log_message,
    )
    broker_adapters = build_runtime_broker_adapters(
        client=client,
        account=account,
        strategy_symbols=tuple(strategy_runtime.managed_symbols),
        account_hash=mask_account_id(account),
        live_orders=not settings.dry_run_only,
        live_order_ack=settings.live_order_ack,
        max_order_notional_usd=settings.max_order_notional_usd,
        cash_only_execution=settings.cash_only_execution,
    )
    market_data_port = broker_adapters.build_market_data_port()
    portfolio_port = broker_adapters.build_portfolio_port()
    execution_port = broker_adapters.build_execution_port()
    snapshot = portfolio_port.get_portfolio_snapshot()
    snapshot = attach_strategy_plugin_metadata(snapshot, strategy_plugin_signals)

    available_inputs = set(strategy_runtime.runtime_adapter.available_inputs)
    benchmark_symbol = str(strategy_runtime.merged_runtime_config.get("benchmark_symbol", "QQQ"))
    market_inputs = build_market_inputs(
        available_inputs=available_inputs,
        market_data_port=market_data_port,
        benchmark_symbol=benchmark_symbol,
        strategy_runtime_config=strategy_runtime.merged_runtime_config,
        strategy_profile=settings.strategy_profile,
        runtime_settings=settings,
        log_message=log_message,
    )
    evaluation_inputs = build_strategy_evaluation_inputs(
        available_inputs=available_inputs,
        market_inputs=market_inputs,
        portfolio_snapshot=snapshot,
        translator=translator,
    )
    evaluation = strategy_runtime.evaluate(**evaluation_inputs)
    plan = map_strategy_decision_to_plan(
        evaluation.decision,
        snapshot=snapshot,
        strategy_profile=settings.strategy_profile,
        runtime_metadata=_runtime_metadata_with_execution_policy(
            getattr(evaluation, "metadata", None),
            settings=settings,
        ),
    )
    plan = substitute_small_safe_haven_targets_with_cash(
        plan,
        threshold_usd=settings.safe_haven_cash_substitute_threshold_usd,
    )
    plan.setdefault("execution", {})["cash_only_execution"] = bool(settings.cash_only_execution)
    run_period = resolve_strategy_run_period(
        now=now,
        plan=plan,
        evaluation_metadata=getattr(evaluation, "metadata", None),
    )
    masked_account = mask_account_id(account)
    existing_run = None
    if persist_strategy_runs and not settings.dry_run_only:
        claim_acquired = claim_live_strategy_run(
            store=store,
            account=masked_account,
            strategy_profile=strategy_runtime.profile,
            run_period=run_period,
            now=now,
        )
        existing_run = read_latest_strategy_run_state(
            store=store,
            account=masked_account,
            strategy_profile=strategy_runtime.profile,
            run_period=run_period,
        )
        if not claim_acquired and existing_run is None:
            existing_run = {
                "stage": "PENDING_SUBMISSION",
                "as_of": now.isoformat(),
                "claim_only": True,
            }
        if not claim_acquired or is_duplicate_live_run(existing_run):
            duplicate_stage = str(existing_run.get("stage") or "NO_ACTION")
            duplicate_skipped_orders = [
                {
                    "reason": "duplicate_live_strategy_run",
                    "run_period": run_period,
                }
            ]
            strategy_run_persisted = False
            strategy_run_persistence_error = None
            duplicate_state = build_strategy_run_state(
                stage=duplicate_stage,
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
                skipped_orders=duplicate_skipped_orders,
                action_done=False,
                now=now,
            )
            duplicate_state["idempotency_skipped"] = True
            duplicate_state["existing_strategy_run_stage"] = existing_run.get("stage")
            duplicate_state["existing_strategy_run_as_of"] = existing_run.get("as_of")
            try:
                strategy_run_persisted = persist_strategy_run_state(
                    store=store,
                    state=duplicate_state,
                    now=now,
                )
            except Exception as exc:
                strategy_run_persisted = False
                strategy_run_persistence_error = f"{type(exc).__name__}: {exc}"
            result = {
                "ok": True,
                "api_kind": "unofficial-reverse-engineered",
                "account": account,
                "strategy_profile": strategy_runtime.profile,
                "strategy_display_name": strategy_runtime.display_name,
                "dry_run_only": settings.dry_run_only,
                "live_trading_enabled": settings.live_trading_enabled,
                "session_reused": bool(getattr(client, "session_reused", False)),
                "strategy_run_period": run_period,
                "strategy_run_stage": duplicate_stage,
                "strategy_run_persisted": strategy_run_persisted,
                "idempotency_skipped": True,
                "existing_strategy_run_stage": existing_run.get("stage"),
                "existing_strategy_run_as_of": existing_run.get("as_of"),
                "submitted_orders": [],
                "skipped_orders": duplicate_skipped_orders,
                "action_done": False,
                **empty_strategy_plugin_alert_report_fields(),
            }
            if strategy_run_persistence_error:
                result["strategy_run_persistence_error"] = strategy_run_persistence_error
            return attach_strategy_plugin_result(
                result,
                signals=strategy_plugin_signals,
                error=strategy_plugin_error,
                translator=translator,
            )
    strategy_plugin_alert_result = None
    strategy_plugin_alert_error = None
    if dispatch_plugin_alerts:
        try:
            strategy_plugin_alert_result = publish_strategy_plugin_alerts(
                strategy_plugin_signals,
                settings=settings,
                translator=translator,
                env_reader=env_reader,
            )
        except Exception as exc:
            strategy_plugin_alert_error = f"{type(exc).__name__}: {exc}"
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
        limit_buy_premium_by_symbol=LIMIT_BUY_PREMIUM_BY_SYMBOL,
        max_order_notional_usd=settings.max_order_notional_usd,
        safe_haven_cash_substitute_threshold_usd=settings.safe_haven_cash_substitute_threshold_usd,
        cash_only_execution=settings.cash_only_execution,
        notional_buy_execution=notional_buy_execution_enabled(settings.strategy_profile),
        fetch_order_status=lambda broker_order_id: client.get_order_status(account, broker_order_id),
    )
    submitted_orders = list(execution_result.submitted_orders)
    skipped_orders = list(execution_result.skipped_orders)
    execution_notes = list(execution_result.execution_notes)
    blocking_skips = filter_execution_blocking_skips(
        skipped_orders,
        blocking_reasons=BROKER_EXECUTION_BLOCKING_SKIP_REASONS,
    )
    execution_blocked = bool(blocking_skips)
    funding_blocked = is_terminal_funding_block(blocking_skips)
    terminal_funding_block = funding_blocked and not execution_result.action_done
    strategy_run_stage = (
        "PENDING_RECONCILIATION"
        if execution_result.pending_reconciliation
        else resolve_strategy_run_stage(
            dry_run_only=settings.dry_run_only,
            execution_blocked=execution_blocked,
            terminal_funding_block=terminal_funding_block,
            action_done=execution_result.action_done,
        )
    )
    signal_snapshot = build_signal_snapshot(
        platform="firstrade",
        strategy_profile=strategy_runtime.profile,
        execution={
            **dict(plan.get("execution", {}) or {}),
            "latest_price_source": "firstrade_ohlc_with_live_quote_overlay",
        },
        allocation=plan.get("allocation", {}),
        metadata=getattr(evaluation, "metadata", None),
    )
    print("signal_snapshot " + json.dumps(signal_snapshot, ensure_ascii=False), flush=True)
    result = {
        "ok": not execution_blocked,
        "api_kind": "unofficial-reverse-engineered",
        "account": account,
        "strategy_profile": strategy_runtime.profile,
        "strategy_display_name": strategy_runtime.display_name,
        "strategy_metadata": getattr(settings, "strategy_metadata", None),
        "dry_run_only": settings.dry_run_only,
        "live_trading_enabled": settings.live_trading_enabled,
        "session_reused": bool(getattr(client, "session_reused", False)),
        "strategy_run_period": run_period,
        "strategy_run_stage": strategy_run_stage,
        "strategy_run_persisted": strategy_run_persisted,
        "portfolio": plan.get("portfolio", {}),
        "allocation": plan.get("allocation", {}),
        "execution": plan.get("execution", {}),
        "signal_snapshot": signal_snapshot,
        "submitted_orders": submitted_orders,
        "skipped_orders": skipped_orders,
        "execution_notes": execution_notes,
        "action_done": execution_result.action_done,
        "broker_submission_done": execution_result.broker_submission_done,
        "execution_status": (
            "pending_reconciliation" if execution_result.pending_reconciliation else ""
        ),
        "orders_pending_count": (
            len(submitted_orders) if execution_result.pending_reconciliation else 0
        ),
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
    if strategy_plugin_alert_result is not None:
        result.update(strategy_plugin_alert_result.to_report_fields())
    else:
        result.update(empty_strategy_plugin_alert_report_fields())
    if strategy_plugin_alert_error:
        result["strategy_plugin_alert_error"] = strategy_plugin_alert_error
    attach_strategy_plugin_result(
        result,
        signals=strategy_plugin_signals,
        error=strategy_plugin_error,
        translator=translator,
    )
    if persist_strategy_runs:
        completed_state = build_strategy_run_state(
            stage=strategy_run_stage,
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
            execution_notes=list(execution_result.execution_notes),
            action_done=execution_result.action_done,
            broker_submission_done=execution_result.broker_submission_done,
            execution_status=result["execution_status"],
            orders_pending_count=result["orders_pending_count"],
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
    if send_cycle_notification and _should_publish_cycle_notification(result):
        try:
            result["notification_sent"] = _publish_cycle_notification(
                result,
                settings=settings,
                notification_sender=notification_sender,
            )
            if not result["notification_sent"]:
                result["notification_error"] = "delivery_not_acknowledged"
        except Exception as exc:
            result["notification_sent"] = False
            result["notification_error"] = f"{type(exc).__name__}: {exc}"
    elif send_cycle_notification:
        result["notification_sent"] = False
        result["notification_suppressed"] = True
        result["notification_suppressed_reason"] = "no_trade_or_error"
    else:
        result["notification_sent"] = False
        result["notification_suppressed"] = True
    try_record_platform_execution(
        strategy_runtime.profile,
        {
            "platform": "firstrade",
            "action_done": result.get("action_done"),
            "broker_submission_done": result.get("broker_submission_done"),
            "execution_status": result.get("execution_status"),
            "orders_pending_count": result.get("orders_pending_count"),
            "strategy_run_stage": result.get("strategy_run_stage"),
            "dry_run_only": settings.dry_run_only,
            "submitted_orders": result.get("submitted_orders"),
            "skipped_orders": result.get("skipped_orders"),
            "error": result.get("error"),
            "total_equity": (result.get("portfolio") or {}).get("total_equity")
            or (result.get("portfolio") or {}).get("equity"),
        },
    )
    return result
