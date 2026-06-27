from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QPK_SRC = ROOT.parent / "QuantPlatformKit" / "src"
UES_SRC = ROOT.parent / "UsEquityStrategies" / "src"


def _has_catalog_marker(candidate: Path, package_name: str, marker: str) -> bool:
    catalog_path = candidate / package_name / "catalog.py"
    if not catalog_path.exists():
        return False
    return marker in catalog_path.read_text(encoding="utf-8")


def _should_add_local_src(candidate: Path) -> bool:
    if candidate == QPK_SRC:
        return (candidate / "quant_platform_kit" / "common" / "runtime_target.py").exists()
    if candidate == UES_SRC:
        return _has_catalog_marker(
            candidate,
            "us_equity_strategies",
            "global_etf_rotation",
        )
    return True


for candidate in (ROOT, QPK_SRC, UES_SRC):
    if not _should_add_local_src(candidate):
        continue
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from strategy_registry import (  # noqa: E402
    FIRSTRADE_PLATFORM,
    get_platform_profile_status_matrix,
    resolve_strategy_definition,
)
from us_equity_strategies.runtime_adapters import (  # noqa: E402
    describe_platform_runtime_requirements,
)


TARGETS_JSON_ENV = "CLOUD_RUN_SERVICE_TARGETS_JSON"
SHARED_TARGET_FALLBACK_ENV = frozenset(
    {
        "GLOBAL_TELEGRAM_CHAT_ID",
        "NOTIFY_LANG",
        "ACCOUNT_PREFIX",
        "ACCOUNT_REGION",
        "EXECUTION_REPORT_GCS_URI",
    }
)
REQUIRED_ENV = ("NOTIFY_LANG",)
OPTIONAL_TARGET_ENV = (
    "GLOBAL_TELEGRAM_CHAT_ID",
    "ACCOUNT_PREFIX",
    "ACCOUNT_REGION",
    "FIRSTRADE_ACCOUNT",
    "FIRSTRADE_COOKIE_DIR",
    "FIRSTRADE_DRY_RUN_ONLY",
    "FIRSTRADE_REUSE_SESSION",
    "FIRSTRADE_SESSION_CACHE_TTL_SECONDS",
    "FIRSTRADE_ENABLE_LIVE_TRADING",
    "FIRSTRADE_RUN_SMOKE_ON_HTTP",
    "FIRSTRADE_RUN_STRATEGY_ON_HTTP",
    "FIRSTRADE_LIVE_ORDER_ACK",
    "FIRSTRADE_MAX_ORDER_NOTIONAL_USD",
    "FIRSTRADE_MIN_RESERVED_CASH_USD",
    "FIRSTRADE_RESERVED_CASH_RATIO",
    "FIRSTRADE_CASH_ONLY_EXECUTION",
    "FIRSTRADE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD",
    "FIRSTRADE_SMOKE_SYMBOL",
    "FIRSTRADE_FEATURE_SNAPSHOT_PATH",
    "FIRSTRADE_FEATURE_SNAPSHOT_MANIFEST_PATH",
    "FIRSTRADE_FEATURE_SNAPSHOT_FALLBACK_MODE",
    "FIRSTRADE_FEATURE_SNAPSHOT_FALLBACK_CACHE_DIR",
    "FIRSTRADE_FEATURE_SNAPSHOT_MAX_STALE_DAYS",
    "FIRSTRADE_GCS_STATE_BUCKET",
    "FIRSTRADE_PERSIST_ACCOUNT_SNAPSHOT",
    "FIRSTRADE_PERSIST_STRATEGY_RUNS",
    "FIRSTRADE_PERSIST_SESSION_CACHE",
    "FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP",
    "FIRSTRADE_SESSION_CHECK_INCLUDE_POSITIONS",
    "FIRSTRADE_STATE_PREFIX",
    "FIRSTRADE_STRATEGY_CONFIG_PATH",
    "FIRSTRADE_STRATEGY_PLUGIN_MOUNTS_JSON",
    "FIRSTRADE_MARKET_SIGNAL_HANDOFF_INDEX_URI",
    "FIRSTRADE_MARKET_SIGNAL_HANDOFF_MANIFEST_URI",
    "FIRSTRADE_MARKET_SIGNAL_CONSUMPTION_AUDIT_URI",
    "FIRSTRADE_MARKET_SIGNAL_CACHE_DIR",
    "FIRSTRADE_MARKET_SIGNAL_REQUIRED",
    "FIRSTRADE_MARKET_SIGNAL_FALLBACK_MODE",
    "FIRSTRADE_MARKET_SIGNAL_MAX_STALE_DAYS",
    "STRATEGY_PLUGIN_ALERT_CHANNELS",
    "STRATEGY_PLUGIN_ALERT_EMAIL_RECIPIENTS",
    "STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_EMAIL",
    "STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD_SECRET_NAME",
    "STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_HOST",
    "STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_PORT",
    "STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_SECURITY",
    "STRATEGY_PLUGIN_ALERT_SMS_RECIPIENTS",
    "STRATEGY_PLUGIN_ALERT_SMS_PROVIDER",
    "STRATEGY_PLUGIN_ALERT_SMS_ACCOUNT_ID",
    "STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN_SECRET_NAME",
    "STRATEGY_PLUGIN_ALERT_SMS_SENDER",
    "STRATEGY_PLUGIN_ALERT_SMS_MESSAGING_SERVICE_ID",
    "STRATEGY_PLUGIN_ALERT_SMS_API_BASE_URL",
    "STRATEGY_PLUGIN_ALERT_SMS_BODY_MAX_CHARS",
    "STRATEGY_PLUGIN_ALERT_PUSH_RECIPIENTS",
    "STRATEGY_PLUGIN_ALERT_PUSH_PROVIDER",
    "STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN_SECRET_NAME",
    "STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN_SECRET_NAME",
    "STRATEGY_PLUGIN_ALERT_PUSH_API_BASE_URL",
    "STRATEGY_PLUGIN_ALERT_PUSH_DEVICE",
    "STRATEGY_PLUGIN_ALERT_PUSH_PRIORITY",
    "STRATEGY_PLUGIN_ALERT_PUSH_TAGS",
    "STRATEGY_PLUGIN_ALERT_PUSH_BODY_MAX_CHARS",
    "STRATEGY_PLUGIN_ALERT_TELEGRAM_CHAT_IDS",
    "STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN_SECRET_NAME",
    "STRATEGY_PLUGIN_ALERT_TELEGRAM_API_BASE_URL",
    "STRATEGY_PLUGIN_ALERT_TELEGRAM_PARSE_MODE",
    "STRATEGY_PLUGIN_ALERT_TELEGRAM_DISABLE_WEB_PAGE_PREVIEW",
    "STRATEGY_PLUGIN_ALERT_TELEGRAM_BODY_MAX_CHARS",
    "FIRSTRADE_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS",
    "FIRSTRADE_TECH_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS",
    "INCOME_THRESHOLD_USD",
    "QQQI_INCOME_RATIO",
    "INCOME_LAYER_ENABLED",
    "INCOME_LAYER_START_USD",
    "INCOME_LAYER_MAX_RATIO",
    "CASH_ONLY_EXECUTION",
    "DCA_MODE",
    "DCA_BASE_INVESTMENT_USD",
    "IBIT_ZSCORE_EXIT_ENABLED",
    "IBIT_ZSCORE_EXIT_MODE",
    "IBIT_ZSCORE_EXIT_PARKING_SYMBOL",
    "IBIT_ZSCORE_EXIT_RISK_REDUCED_EXPOSURE",
    "IBIT_ZSCORE_EXIT_RISK_OFF_EXPOSURE",
    "IBIT_ZSCORE_EXIT_ALLOW_OUTSIDE_EXECUTION_WINDOW",
    "RUNTIME_TARGET_ENABLED",
    "EXECUTION_REPORT_GCS_URI",
)
SCHEDULER_TIME_DEFAULTS = {
    "main_time": "45 15",
    "probe_time": "35 9,15",
    "precheck_time": "45 9",
}
SCHEDULER_TIME_ENV = {
    "main_time": "CLOUD_SCHEDULER_MAIN_TIME",
    "probe_time": "CLOUD_SCHEDULER_PROBE_TIME",
    "precheck_time": "CLOUD_SCHEDULER_PRECHECK_TIME",
}


def build_sync_plan(env: Mapping[str, str] = os.environ) -> dict[str, object]:
    target_entries, defaults, per_service_mode = _load_target_entries(env)
    status_rows = {
        str(row["canonical_profile"]): {
            **row,
            **describe_platform_runtime_requirements(
                str(row["canonical_profile"]),
                platform_id=FIRSTRADE_PLATFORM,
            ),
        }
        for row in get_platform_profile_status_matrix()
    }
    planned_targets = [
        _build_target_plan(
            target=target,
            defaults=defaults,
            env=env,
            status_rows=status_rows,
            per_service_mode=per_service_mode,
        )
        for target in target_entries
    ]
    if not planned_targets:
        raise ValueError(
            f"{TARGETS_JSON_ENV}, CLOUD_RUN_SERVICES, or CLOUD_RUN_SERVICE is required"
        )
    return {
        "mode": "per_service" if per_service_mode else "legacy",
        "targets": planned_targets,
    }


def _load_target_entries(
    env: Mapping[str, str],
) -> tuple[list[dict[str, object]], Mapping[str, object], bool]:
    raw_targets = str(env.get(TARGETS_JSON_ENV, "") or "").strip()
    if raw_targets:
        payload = json.loads(raw_targets)
        if isinstance(payload, Mapping):
            raw_entries = payload.get("targets")
            defaults = _coerce_mapping(payload.get("defaults") or {})
        else:
            raw_entries = payload
            defaults = {}
        if not isinstance(raw_entries, Sequence) or isinstance(raw_entries, (str, bytes)):
            raise ValueError(f"{TARGETS_JSON_ENV} must be a JSON array or object with targets")
        entries = [_coerce_mapping(item) for item in raw_entries]
        return entries, defaults, True

    raw_services = str(env.get("CLOUD_RUN_SERVICES", "") or "").strip()
    if not raw_services:
        raw_services = str(env.get("CLOUD_RUN_SERVICE", "") or "").strip()
    services = [
        item.strip()
        for chunk in raw_services.replace(";", ",").replace("\n", ",").split(",")
        for item in [chunk]
        if item.strip()
    ]
    return [{"service": service} for service in services], {}, False


def _build_target_plan(
    *,
    target: Mapping[str, object],
    defaults: Mapping[str, object],
    env: Mapping[str, str],
    status_rows: Mapping[str, Mapping[str, object]],
    per_service_mode: bool,
) -> dict[str, object]:
    service_name = _first_non_empty(
        _target_field(target, defaults, "service"),
        _target_field(target, defaults, "service_name"),
        _target_field(target, defaults, "cloud_run_service"),
    )
    runtime_target = _resolve_runtime_target(target, defaults, env, per_service_mode)
    if not service_name:
        service_name = str(runtime_target.get("service_name") or "").strip()
    if not service_name:
        raise ValueError("Each Cloud Run sync target requires service/service_name")

    runtime_target_service = str(runtime_target.get("service_name") or "").strip()
    if runtime_target_service and runtime_target_service != service_name:
        raise ValueError(
            f"Target {service_name} runtime_target.service_name={runtime_target_service!r} does not match"
        )
    runtime_target["service_name"] = service_name

    raw_profile = str(runtime_target.get("strategy_profile") or "").strip()
    if not raw_profile:
        raise ValueError(f"Target {service_name} runtime_target.strategy_profile is required")
    definition = resolve_strategy_definition(raw_profile, platform_id=FIRSTRADE_PLATFORM)
    canonical_profile = definition.profile
    runtime_target["strategy_profile"] = canonical_profile

    status = status_rows.get(canonical_profile)
    if status is None:
        supported = ", ".join(sorted(status_rows))
        raise ValueError(
            f"Unsupported STRATEGY_PROFILE={raw_profile!r} for {service_name}; supported: {supported}"
        )
    if not status.get("eligible") or not status.get("enabled"):
        raise ValueError(
            f"STRATEGY_PROFILE={raw_profile!r} is not eligible/enabled for {service_name}: {status}"
        )

    env_values: dict[str, str] = {}
    missing: list[str] = []
    for name in REQUIRED_ENV:
        value = _target_env_value(
            target,
            defaults,
            env,
            name,
            per_service_mode=per_service_mode,
            allow_shared_fallback=name in SHARED_TARGET_FALLBACK_ENV,
        )
        if value is None:
            missing.append(f"{service_name}:{name}")
        else:
            env_values[name] = value

    env_values["STRATEGY_PROFILE"] = canonical_profile
    env_values["RUNTIME_TARGET_JSON"] = json.dumps(
        runtime_target,
        separators=(",", ":"),
        sort_keys=True,
    )

    remove_env_vars: list[str] = []
    for name in OPTIONAL_TARGET_ENV:
        value = _target_env_value(
            target,
            defaults,
            env,
            name,
            per_service_mode=per_service_mode,
            allow_shared_fallback=name in SHARED_TARGET_FALLBACK_ENV,
        )
        if value is None and name == "FIRSTRADE_DRY_RUN_ONLY":
            dry_run_value = runtime_target.get("dry_run_only")
            if dry_run_value is not None:
                value = _coerce_env_value(dry_run_value)
        if value is None:
            remove_env_vars.append(name)
        else:
            env_values[name] = value

    if _runtime_target_enabled(env_values):
        _validate_profile_inputs(
            service_name=service_name,
            env_values=env_values,
            status=status,
            missing=missing,
        )
    if missing:
        raise ValueError(
            "Cloud Run env sync target values are missing:\n"
            + "\n".join(f"  - {item}" for item in missing)
        )

    return {
        "service_name": service_name,
        "strategy_profile": canonical_profile,
        "env": env_values,
        "scheduler": _build_scheduler_plan(
            runtime_target=runtime_target,
            target=target,
            defaults=defaults,
            env=env,
            per_service_mode=per_service_mode,
        ),
        "remove_env_vars": sorted(set(remove_env_vars) - set(env_values)),
    }


def _build_scheduler_plan(
    *,
    runtime_target: Mapping[str, object],
    target: Mapping[str, object],
    defaults: Mapping[str, object],
    env: Mapping[str, str],
    per_service_mode: bool,
) -> dict[str, str]:
    runtime_scheduler = runtime_target.get("scheduler") if isinstance(runtime_target, Mapping) else {}
    if not isinstance(runtime_scheduler, Mapping):
        runtime_scheduler = {}
    timezone = str(runtime_scheduler.get("timezone") or "").strip()
    if not timezone:
        timezone = "America/New_York"

    scheduler = {"timezone": timezone}
    for key, env_name in SCHEDULER_TIME_ENV.items():
        configured_value = _target_env_value(
            target,
            defaults,
            env,
            env_name,
            per_service_mode=per_service_mode,
            allow_shared_fallback=True,
        )
        scheduler[key] = str(runtime_scheduler.get(key) or configured_value or SCHEDULER_TIME_DEFAULTS[key])
    return scheduler


def _validate_profile_inputs(
    *,
    service_name: str,
    env_values: Mapping[str, str],
    status: Mapping[str, object],
    missing: list[str],
) -> None:
    if bool(status.get("requires_snapshot_artifacts")) and not env_values.get(
        "FIRSTRADE_FEATURE_SNAPSHOT_PATH"
    ):
        missing.append(f"{service_name}:FIRSTRADE_FEATURE_SNAPSHOT_PATH")
    if bool(status.get("requires_snapshot_manifest_path")) and not env_values.get(
        "FIRSTRADE_FEATURE_SNAPSHOT_MANIFEST_PATH"
    ):
        missing.append(f"{service_name}:FIRSTRADE_FEATURE_SNAPSHOT_MANIFEST_PATH")
    if (
        bool(status.get("requires_strategy_config_path"))
        and str(status.get("config_source_policy") or "none") == "env_only"
        and not env_values.get("FIRSTRADE_STRATEGY_CONFIG_PATH")
    ):
        missing.append(f"{service_name}:FIRSTRADE_STRATEGY_CONFIG_PATH")


def _runtime_target_enabled(env_values: Mapping[str, str]) -> bool:
    raw = str(env_values.get("RUNTIME_TARGET_ENABLED") or "").strip().lower()
    if not raw:
        return True
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError("RUNTIME_TARGET_ENABLED must be true or false")


def _resolve_runtime_target(
    target: Mapping[str, object],
    defaults: Mapping[str, object],
    env: Mapping[str, str],
    per_service_mode: bool,
) -> dict[str, object]:
    raw = _first_non_empty(
        _target_field(target, defaults, "runtime_target"),
        _target_field(target, defaults, "runtime_target_json"),
    )
    if raw is None and not per_service_mode:
        raw = str(env.get("RUNTIME_TARGET_JSON", "") or "").strip() or None
    if raw is None:
        raise ValueError(
            f"{TARGETS_JSON_ENV}.targets[].runtime_target is required in per-service mode"
        )
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        loaded = json.loads(raw)
        if not isinstance(loaded, Mapping):
            raise ValueError("runtime_target_json must decode to a JSON object")
        return dict(loaded)
    raise ValueError("runtime_target must be a JSON object or JSON object string")


def _target_env_value(
    target: Mapping[str, object],
    defaults: Mapping[str, object],
    env: Mapping[str, str],
    env_name: str,
    *,
    per_service_mode: bool,
    allow_shared_fallback: bool,
) -> str | None:
    raw = _first_non_empty(
        _target_field(target, defaults, env_name),
        _target_field(target, defaults, env_name.lower()),
    )
    if raw is None and (not per_service_mode or allow_shared_fallback):
        raw = str(env.get(env_name, "") or "").strip() or None
    return _coerce_env_value(raw)


def _target_field(
    target: Mapping[str, object],
    defaults: Mapping[str, object],
    name: str,
) -> object | None:
    for source in (target, _coerce_mapping(target.get("env") or {}), defaults, _coerce_mapping(defaults.get("env") or {})):
        if name in source:
            return source[name]
    return None


def _coerce_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("Expected a JSON object")
    return value


def _coerce_env_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    text = str(value).strip()
    return text or None


def _first_non_empty(*values: object | None) -> object | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Print compact JSON.")
    args = parser.parse_args()

    try:
        plan = build_sync_plan()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(plan, separators=(",", ":"), sort_keys=True))
    else:
        print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
