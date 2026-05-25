from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from quant_platform_kit.common.runtime_config import (
    resolve_bool_value,
    resolve_optional_float_env,
    resolve_strategy_runtime_path_settings,
)
from quant_platform_kit.common.runtime_target import (
    RuntimeTarget,
    build_runtime_target,
    resolve_runtime_target_from_env,
)
from strategy_registry import (
    FIRSTRADE_PLATFORM,
    resolve_strategy_definition,
    resolve_strategy_metadata,
)
from us_equity_strategies import get_strategy_catalog

DEFAULT_ACCOUNT_REGION = "US"
DEFAULT_RESERVED_CASH_FLOOR_USD = 0.0
DEFAULT_RESERVED_CASH_RATIO = 0.0
DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD = 1000.0


@dataclass(frozen=True)
class PlatformRuntimeSettings:
    project_id: str | None
    account_prefix: str
    account_region: str
    strategy_profile: str
    strategy_display_name: str
    strategy_domain: str
    notify_lang: str
    tg_token: str | None
    tg_chat_id: str | None
    dry_run_only: bool
    live_trading_enabled: bool
    run_strategy_on_http: bool
    live_order_ack: bool
    max_order_notional_usd: float | None
    reserved_cash_floor_usd: float = DEFAULT_RESERVED_CASH_FLOOR_USD
    reserved_cash_ratio: float = DEFAULT_RESERVED_CASH_RATIO
    persist_strategy_runs: bool = False
    safe_haven_cash_substitute_threshold_usd: float = DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD
    debug_position_snapshot: bool = False
    income_threshold_usd: float | None = None
    qqqi_income_ratio: float | None = None
    runtime_execution_window_trading_days: int | None = None
    feature_snapshot_path: str | None = None
    feature_snapshot_manifest_path: str | None = None
    strategy_config_path: str | None = None
    strategy_config_source: str | None = None
    strategy_plugin_mounts_json: str | None = None
    crisis_alert_channels: tuple[str, ...] = ()
    crisis_alert_email_recipients: tuple[str, ...] = ()
    crisis_alert_email_sender_email: str | None = None
    crisis_alert_email_sender_password: str | None = None
    crisis_alert_email_smtp_host: str | None = None
    crisis_alert_email_smtp_port: str | None = None
    crisis_alert_email_smtp_security: str | None = None
    crisis_alert_sms_recipients: tuple[str, ...] = ()
    crisis_alert_sms_provider: str | None = None
    crisis_alert_sms_account_id: str | None = None
    crisis_alert_sms_auth_token: str | None = None
    crisis_alert_sms_sender: str | None = None
    crisis_alert_sms_messaging_service_id: str | None = None
    crisis_alert_sms_api_base_url: str | None = None
    crisis_alert_sms_body_max_chars: str | None = None
    crisis_alert_push_recipients: tuple[str, ...] = ()
    crisis_alert_push_provider: str | None = None
    crisis_alert_push_app_token: str | None = None
    crisis_alert_push_access_token: str | None = None
    crisis_alert_push_api_base_url: str | None = None
    crisis_alert_push_device: str | None = None
    crisis_alert_push_priority: str | None = None
    crisis_alert_push_tags: str | None = None
    crisis_alert_push_body_max_chars: str | None = None
    crisis_alert_telegram_chat_ids: tuple[str, ...] = ()
    crisis_alert_telegram_bot_token: str | None = None
    crisis_alert_telegram_api_base_url: str | None = None
    crisis_alert_telegram_parse_mode: str | None = None
    crisis_alert_telegram_disable_web_page_preview: str | None = None
    crisis_alert_telegram_body_max_chars: str | None = None
    runtime_target: RuntimeTarget | None = None


def infer_account_region(raw_value: str | None, *, account_prefix: str) -> str:
    for candidate in (raw_value, account_prefix):
        normalized = _normalize_region(candidate)
        if normalized is not None:
            return normalized
    return DEFAULT_ACCOUNT_REGION


def load_platform_runtime_settings(
    *,
    project_id_resolver: Callable[[], str | None],
) -> PlatformRuntimeSettings:
    dry_run_only = resolve_bool_value(os.getenv("FIRSTRADE_DRY_RUN_ONLY", "true"))
    account_prefix = os.getenv("ACCOUNT_PREFIX", "FIRSTRADE")
    safe_haven_cash_substitute_threshold_usd = resolve_optional_float_env(
        os.environ,
        "FIRSTRADE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD",
    )
    runtime_target = _resolve_runtime_target(dry_run_only=dry_run_only)
    strategy_definition = resolve_strategy_definition(
        runtime_target.strategy_profile,
        platform_id=FIRSTRADE_PLATFORM,
    )
    strategy_metadata = resolve_strategy_metadata(
        strategy_definition.profile,
        platform_id=FIRSTRADE_PLATFORM,
    )
    runtime_paths = resolve_strategy_runtime_path_settings(
        strategy_catalog=get_strategy_catalog(),
        strategy_definition=strategy_definition,
        strategy_metadata=strategy_metadata,
        platform_env_prefix="FIRSTRADE",
        env=os.environ,
        repo_root=Path(__file__).resolve().parent,
    )
    return PlatformRuntimeSettings(
        project_id=project_id_resolver(),
        account_prefix=account_prefix,
        account_region=infer_account_region(
            os.getenv("ACCOUNT_REGION"),
            account_prefix=account_prefix,
        ),
        strategy_profile=runtime_paths.strategy_profile,
        strategy_display_name=runtime_paths.strategy_display_name,
        strategy_domain=runtime_paths.strategy_domain,
        notify_lang=os.getenv("NOTIFY_LANG", "en"),
        tg_token=os.getenv("TELEGRAM_TOKEN"),
        tg_chat_id=os.getenv("GLOBAL_TELEGRAM_CHAT_ID"),
        dry_run_only=dry_run_only,
        live_trading_enabled=resolve_bool_value(os.getenv("FIRSTRADE_ENABLE_LIVE_TRADING")),
        run_strategy_on_http=resolve_bool_value(os.getenv("FIRSTRADE_RUN_STRATEGY_ON_HTTP")),
        live_order_ack=resolve_bool_value(os.getenv("FIRSTRADE_LIVE_ORDER_ACK")),
        persist_strategy_runs=resolve_bool_value(os.getenv("FIRSTRADE_PERSIST_STRATEGY_RUNS")),
        max_order_notional_usd=resolve_optional_float_env(
            os.environ,
            "FIRSTRADE_MAX_ORDER_NOTIONAL_USD",
        ),
        reserved_cash_floor_usd=_resolve_non_negative_float_env(
            "FIRSTRADE_MIN_RESERVED_CASH_USD",
            default=DEFAULT_RESERVED_CASH_FLOOR_USD,
        ),
        reserved_cash_ratio=_resolve_ratio_env(
            "FIRSTRADE_RESERVED_CASH_RATIO",
            default=DEFAULT_RESERVED_CASH_RATIO,
        ),
        safe_haven_cash_substitute_threshold_usd=(
            max(0.0, safe_haven_cash_substitute_threshold_usd)
            if safe_haven_cash_substitute_threshold_usd is not None
            else DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD
        ),
        debug_position_snapshot=resolve_bool_value(os.getenv("FIRSTRADE_DEBUG_POSITION_SNAPSHOT")),
        income_threshold_usd=resolve_optional_float_env(os.environ, "INCOME_THRESHOLD_USD"),
        qqqi_income_ratio=_qqqi_income_ratio_env(),
        runtime_execution_window_trading_days=_runtime_execution_window_trading_days_env(
            strategy_definition.profile
        ),
        feature_snapshot_path=runtime_paths.feature_snapshot_path,
        feature_snapshot_manifest_path=runtime_paths.feature_snapshot_manifest_path,
        strategy_config_path=runtime_paths.strategy_config_path,
        strategy_config_source=runtime_paths.strategy_config_source,
        strategy_plugin_mounts_json=(
            os.getenv("FIRSTRADE_STRATEGY_PLUGIN_MOUNTS_JSON")
            or os.getenv("STRATEGY_PLUGIN_MOUNTS_JSON")
        ),
        crisis_alert_channels=_split_env_list(os.getenv("CRISIS_ALERT_CHANNELS")),
        crisis_alert_email_recipients=_split_env_list(os.getenv("CRISIS_ALERT_EMAIL_RECIPIENTS")),
        crisis_alert_email_sender_email=_first_non_empty(os.getenv("CRISIS_ALERT_EMAIL_SENDER_EMAIL")),
        crisis_alert_email_sender_password=_first_non_empty(
            os.getenv("CRISIS_ALERT_EMAIL_SENDER_PASSWORD")
        ),
        crisis_alert_email_smtp_host=_first_non_empty(os.getenv("CRISIS_ALERT_EMAIL_SMTP_HOST")),
        crisis_alert_email_smtp_port=_first_non_empty(os.getenv("CRISIS_ALERT_EMAIL_SMTP_PORT")),
        crisis_alert_email_smtp_security=_first_non_empty(
            os.getenv("CRISIS_ALERT_EMAIL_SMTP_SECURITY")
        ),
        crisis_alert_sms_recipients=_split_env_list(os.getenv("CRISIS_ALERT_SMS_RECIPIENTS")),
        crisis_alert_sms_provider=_first_non_empty(os.getenv("CRISIS_ALERT_SMS_PROVIDER")),
        crisis_alert_sms_account_id=_first_non_empty(os.getenv("CRISIS_ALERT_SMS_ACCOUNT_ID")),
        crisis_alert_sms_auth_token=_first_non_empty(os.getenv("CRISIS_ALERT_SMS_AUTH_TOKEN")),
        crisis_alert_sms_sender=_first_non_empty(os.getenv("CRISIS_ALERT_SMS_SENDER")),
        crisis_alert_sms_messaging_service_id=_first_non_empty(
            os.getenv("CRISIS_ALERT_SMS_MESSAGING_SERVICE_ID")
        ),
        crisis_alert_sms_api_base_url=_first_non_empty(os.getenv("CRISIS_ALERT_SMS_API_BASE_URL")),
        crisis_alert_sms_body_max_chars=_first_non_empty(
            os.getenv("CRISIS_ALERT_SMS_BODY_MAX_CHARS")
        ),
        crisis_alert_push_recipients=_split_env_list(os.getenv("CRISIS_ALERT_PUSH_RECIPIENTS")),
        crisis_alert_push_provider=_first_non_empty(os.getenv("CRISIS_ALERT_PUSH_PROVIDER")),
        crisis_alert_push_app_token=_first_non_empty(os.getenv("CRISIS_ALERT_PUSH_APP_TOKEN")),
        crisis_alert_push_access_token=_first_non_empty(os.getenv("CRISIS_ALERT_PUSH_ACCESS_TOKEN")),
        crisis_alert_push_api_base_url=_first_non_empty(os.getenv("CRISIS_ALERT_PUSH_API_BASE_URL")),
        crisis_alert_push_device=_first_non_empty(os.getenv("CRISIS_ALERT_PUSH_DEVICE")),
        crisis_alert_push_priority=_first_non_empty(os.getenv("CRISIS_ALERT_PUSH_PRIORITY")),
        crisis_alert_push_tags=_first_non_empty(os.getenv("CRISIS_ALERT_PUSH_TAGS")),
        crisis_alert_push_body_max_chars=_first_non_empty(
            os.getenv("CRISIS_ALERT_PUSH_BODY_MAX_CHARS")
        ),
        crisis_alert_telegram_chat_ids=_split_env_list(
            os.getenv("CRISIS_ALERT_TELEGRAM_CHAT_IDS")
        ),
        crisis_alert_telegram_bot_token=_first_non_empty(
            os.getenv("CRISIS_ALERT_TELEGRAM_BOT_TOKEN")
        ),
        crisis_alert_telegram_api_base_url=_first_non_empty(
            os.getenv("CRISIS_ALERT_TELEGRAM_API_BASE_URL")
        ),
        crisis_alert_telegram_parse_mode=_first_non_empty(
            os.getenv("CRISIS_ALERT_TELEGRAM_PARSE_MODE")
        ),
        crisis_alert_telegram_disable_web_page_preview=_first_non_empty(
            os.getenv("CRISIS_ALERT_TELEGRAM_DISABLE_WEB_PAGE_PREVIEW")
        ),
        crisis_alert_telegram_body_max_chars=_first_non_empty(
            os.getenv("CRISIS_ALERT_TELEGRAM_BODY_MAX_CHARS")
        ),
        runtime_target=runtime_target,
    )


def _resolve_runtime_target(*, dry_run_only: bool) -> RuntimeTarget:
    if os.getenv("RUNTIME_TARGET_JSON"):
        return resolve_runtime_target_from_env(
            env=os.environ,
            expected_platform_id=FIRSTRADE_PLATFORM,
        )
    profile = os.getenv("STRATEGY_PROFILE")
    if not profile:
        raise EnvironmentError("STRATEGY_PROFILE or RUNTIME_TARGET_JSON is required")
    return build_runtime_target(
        platform_id=FIRSTRADE_PLATFORM,
        strategy_profile=profile,
        dry_run_only=dry_run_only,
        deployment_selector=os.getenv("K_SERVICE"),
        account_selector=os.getenv("FIRSTRADE_ACCOUNT"),
        account_scope=os.getenv("ACCOUNT_REGION") or DEFAULT_ACCOUNT_REGION,
        service_name=os.getenv("K_SERVICE"),
    )


def _normalize_region(raw_value: str | None) -> str | None:
    if raw_value is None:
        return None
    value = str(raw_value).strip()
    if not value:
        return None
    return value.upper()


def _qqqi_income_ratio_env() -> float | None:
    value = resolve_optional_float_env(os.environ, "QQQI_INCOME_RATIO")
    if value is not None and not (0.0 <= value <= 1.0):
        raise ValueError(f"QQQI_INCOME_RATIO must be in [0,1], got {value}")
    return value


def _resolve_non_negative_float_env(name: str, *, default: float) -> float:
    value = resolve_optional_float_env(os.environ, name)
    if value is None:
        return float(default)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value}")
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value}")
    return float(value)


def _resolve_ratio_env(name: str, *, default: float) -> float:
    value = _resolve_non_negative_float_env(name, default=default)
    if value > 1.0:
        raise ValueError(f"{name} must be in [0,1], got {value}")
    return value


def _first_non_empty(*raw_values: str | None) -> str | None:
    for raw_value in raw_values:
        value = str(raw_value or "").strip()
        if value:
            return value
    return None


def _split_env_list(raw_value: str | None) -> tuple[str, ...]:
    if raw_value is None:
        return ()
    items = []
    seen = set()
    for value in str(raw_value).replace(";", ",").replace("\n", ",").split(","):
        item = value.strip()
        if not item or item in seen:
            continue
        items.append(item)
        seen.add(item)
    return tuple(items)


def _runtime_execution_window_trading_days_env(strategy_profile: str) -> int | None:
    raw_value = os.getenv("FIRSTRADE_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS")
    env_name = "FIRSTRADE_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS"
    if raw_value is None and strategy_profile == "tech_communication_pullback_enhancement":
        raw_value = os.getenv("FIRSTRADE_TECH_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS")
        env_name = "FIRSTRADE_TECH_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS"
    if raw_value is None or not str(raw_value).strip():
        return None
    try:
        value = int(str(raw_value).strip())
    except ValueError as exc:
        raise ValueError(f"{env_name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{env_name} must be a positive integer")
    return value
