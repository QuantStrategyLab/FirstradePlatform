"""Read-only Firstrade session and account-state checks."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from application.account_payload_utils import (
    float_or_none,
    get_first,
    iter_position_rows,
    selected_numeric_metrics,
)
from application.firstrade_client import (
    FirstradeBrokerClient,
    FirstradeCredentials,
    FirstradeMfaRequired,
    is_live_trading_enabled,
    mask_account_id,
)
from application.state_persistence import GcsStateStore, build_gcs_state_store_from_env
from strategy_registry import (
    FIRSTRADE_PLATFORM,
    resolve_strategy_definition,
    resolve_strategy_metadata,
)

BALANCE_KEYWORDS = (
    "cash",
    "available",
    "avail",
    "withdraw",
    "buying",
    "bp",
    "equity",
    "value",
    "margin",
)
_FEATURE_SNAPSHOT_INPUT = "feature_snapshot"
_SESSION_CHECK_POLICY_DEFAULT = "auto"
_SESSION_CHECK_POLICY_SKIP_VALUES = frozenset({"skip", "never", "disabled", "off", "false", "0"})
_SESSION_CHECK_POLICY_ALWAYS_VALUES = frozenset({"always", "run", "true", "1"})
_DIAGNOSTIC_SCHEMA_KEY_CODES = {
    "cash": "cash",
    "cash balance": "cash_balance",
    "cash_balance": "cash_balance",
    "available": "available",
    "available cash": "available_cash",
    "available_cash": "available_cash",
    "settled cash": "settled_cash",
    "settled_cash": "settled_cash",
    "unsettled cash": "unsettled_cash",
    "unsettled_cash": "unsettled_cash",
    "balance": "balance",
    "balances": "balances",
    "value": "value",
    "total": "total",
    "total value": "total_value",
    "total_value": "total_value",
    "total account value": "total_account_value",
    "equity": "equity",
    "net liquidation": "net_liquidation",
    "buying power": "buying_power",
    "buying_power": "buying_power",
    "margin": "margin",
    "label": "label",
    "display label": "display_label",
    "display_label": "display_label",
    "display name": "display_name",
    "display_name": "display_name",
    "displayname": "display_name",
    "unit": "unit",
    "currency": "currency",
    "currency code": "currency_code",
    "currency_code": "currency_code",
    "currencycode": "currency_code",
}
_DIAGNOSTIC_LABELS = {
    "Total Account Value": "total_account_value",
    "Cash Balance": "cash_balance",
    "Available Cash": "available_cash",
}
_DIAGNOSTIC_LABEL_KEYS = frozenset({"label", "display_label", "displayname", "display_name"})
_DIAGNOSTIC_UNIT_KEYS = frozenset({"unit", "currency", "currency_code", "currencycode"})


@dataclass(frozen=True)
class SessionCheckMaintenanceDecision:
    should_run: bool
    policy: str
    strategy_profile: str | None = None
    strategy_cadence: str | None = None
    strategy_required_inputs: tuple[str, ...] = ()
    period: str | None = None
    state_key: str | None = None
    reason: str = ""
    last_checked_at: str | None = None
    diagnostic_error: str | None = None

    def to_response_fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "session_check_policy": self.policy,
            "session_check_policy_reason": self.reason,
        }
        if self.strategy_profile:
            fields["strategy_profile"] = self.strategy_profile
        if self.strategy_cadence:
            fields["strategy_cadence"] = self.strategy_cadence
        if self.strategy_required_inputs:
            fields["strategy_required_inputs"] = list(self.strategy_required_inputs)
        if self.period:
            fields["session_check_period"] = self.period
        if self.last_checked_at:
            fields["session_check_last_checked_at"] = self.last_checked_at
        if self.diagnostic_error:
            fields["session_check_policy_error"] = self.diagnostic_error
        return fields


def _flag(name: str, default: str = "false", env: Callable[[str, str | None], str | None] = os.getenv) -> bool:
    return (env(name, default) or "").strip().lower() == "true"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_key(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(value or "")) or "unknown"


def _normalize_session_check_policy(raw_value: str | None) -> str:
    value = str(raw_value or _SESSION_CHECK_POLICY_DEFAULT).strip().lower()
    if not value or value == "auto":
        return "auto"
    if value in _SESSION_CHECK_POLICY_ALWAYS_VALUES:
        return "always"
    if value in _SESSION_CHECK_POLICY_SKIP_VALUES:
        return "skip"
    return "auto"


def _configured_strategy_profile(
    env: Callable[[str, str | None], str | None] = os.getenv,
) -> str | None:
    raw_runtime_target = env("RUNTIME_TARGET_JSON", None)
    if raw_runtime_target:
        payload = json.loads(raw_runtime_target)
        if not isinstance(payload, dict):
            raise ValueError("RUNTIME_TARGET_JSON must decode to an object")
        raw_profile = payload.get("strategy_profile")
        profile = str(raw_profile or "").strip()
        if profile:
            return profile
    profile = str(env("STRATEGY_PROFILE", "") or "").strip()
    return profile or None


def _resolve_strategy_session_check_context(
    env: Callable[[str, str | None], str | None] = os.getenv,
) -> tuple[str | None, str | None, tuple[str, ...], str | None]:
    try:
        raw_profile = _configured_strategy_profile(env)
        if not raw_profile:
            return None, None, (), "STRATEGY_PROFILE or RUNTIME_TARGET_JSON is not configured"
        definition = resolve_strategy_definition(raw_profile, platform_id=FIRSTRADE_PLATFORM)
        metadata = resolve_strategy_metadata(definition.profile, platform_id=FIRSTRADE_PLATFORM)
        required_inputs = tuple(sorted(str(value) for value in definition.required_inputs))
        return definition.profile, metadata.cadence, required_inputs, None
    except Exception as exc:
        return None, None, (), f"{type(exc).__name__}: {exc}"


def _session_check_period(
    *,
    cadence: str | None,
    required_inputs: tuple[str, ...],
    now: datetime,
) -> tuple[str | None, str]:
    cadence_text = str(cadence or "").strip().lower()
    required_input_set = frozenset(required_inputs)
    if "daily" in cadence_text or "intraday" in cadence_text:
        return None, "daily_strategy"
    if "weekly" in cadence_text:
        return f"{now:%G}-W{now:%V}", "weekly_strategy"
    if "monthly" in cadence_text:
        return now.strftime("%Y-%m"), "monthly_strategy"
    if "quarterly" in cadence_text:
        quarter = ((now.month - 1) // 3) + 1
        return f"{now.year}-Q{quarter}", "quarterly_strategy"
    if _FEATURE_SNAPSHOT_INPUT in required_input_set:
        return now.strftime("%Y-%m"), "feature_snapshot_strategy"
    return None, "unthrottled_strategy"


def _session_check_state_key(
    *,
    strategy_profile: str,
    period: str,
    env: Callable[[str, str | None], str | None] = os.getenv,
) -> str:
    account_selector = str(env("FIRSTRADE_ACCOUNT", "") or "").strip()
    account_key = _safe_key(mask_account_id(account_selector)) if account_selector else "auto"
    return (
        f"session-checks/{account_key}/{_safe_key(strategy_profile)}/"
        f"{_safe_key(period)}/latest.json"
    )


def resolve_session_check_maintenance_decision(
    *,
    state_store: GcsStateStore | None = None,
    env_reader: Callable[[str, str | None], str | None] = os.getenv,
    now: datetime | None = None,
) -> SessionCheckMaintenanceDecision:
    as_of = now or _utcnow()
    policy = _normalize_session_check_policy(env_reader("FIRSTRADE_SESSION_CHECK_POLICY", "auto"))
    strategy_profile, cadence, required_inputs, strategy_error = _resolve_strategy_session_check_context(
        env_reader
    )
    if policy == "skip":
        return SessionCheckMaintenanceDecision(
            should_run=False,
            policy=policy,
            strategy_profile=strategy_profile,
            strategy_cadence=cadence,
            strategy_required_inputs=required_inputs,
            reason="policy_skip",
            diagnostic_error=strategy_error,
        )
    if policy == "always":
        return SessionCheckMaintenanceDecision(
            should_run=True,
            policy=policy,
            strategy_profile=strategy_profile,
            strategy_cadence=cadence,
            strategy_required_inputs=required_inputs,
            reason="policy_always",
            diagnostic_error=strategy_error,
        )
    if strategy_error or not strategy_profile:
        return SessionCheckMaintenanceDecision(
            should_run=True,
            policy=policy,
            strategy_profile=strategy_profile,
            strategy_cadence=cadence,
            strategy_required_inputs=required_inputs,
            reason="strategy_context_unavailable",
            diagnostic_error=strategy_error,
        )

    period, period_reason = _session_check_period(
        cadence=cadence,
        required_inputs=required_inputs,
        now=as_of,
    )
    if period is None:
        return SessionCheckMaintenanceDecision(
            should_run=True,
            policy=policy,
            strategy_profile=strategy_profile,
            strategy_cadence=cadence,
            strategy_required_inputs=required_inputs,
            reason=period_reason,
        )
    state_key = _session_check_state_key(
        strategy_profile=strategy_profile,
        period=period,
        env=env_reader,
    )
    if state_store is None:
        return SessionCheckMaintenanceDecision(
            should_run=True,
            policy=policy,
            strategy_profile=strategy_profile,
            strategy_cadence=cadence,
            strategy_required_inputs=required_inputs,
            period=period,
            state_key=state_key,
            reason="state_store_unavailable",
        )
    try:
        existing = state_store.read_json(state_key)
    except Exception as exc:
        return SessionCheckMaintenanceDecision(
            should_run=True,
            policy=policy,
            strategy_profile=strategy_profile,
            strategy_cadence=cadence,
            strategy_required_inputs=required_inputs,
            period=period,
            state_key=state_key,
            reason="state_read_failed",
            diagnostic_error=f"{type(exc).__name__}: {exc}",
        )
    if existing:
        return SessionCheckMaintenanceDecision(
            should_run=False,
            policy=policy,
            strategy_profile=strategy_profile,
            strategy_cadence=cadence,
            strategy_required_inputs=required_inputs,
            period=period,
            state_key=state_key,
            reason=period_reason,
            last_checked_at=str(existing.get("checked_at") or "") or None,
        )
    return SessionCheckMaintenanceDecision(
        should_run=True,
        policy=policy,
        strategy_profile=strategy_profile,
        strategy_cadence=cadence,
        strategy_required_inputs=required_inputs,
        period=period,
        state_key=state_key,
        reason=f"{period_reason}_due",
    )


def persist_session_check_maintenance(
    *,
    store: GcsStateStore,
    decision: SessionCheckMaintenanceDecision,
    account: str,
    session_reused: bool,
    now: datetime | None = None,
) -> bool:
    if not decision.state_key:
        return False
    as_of = now or _utcnow()
    payload = {
        "checked_at": as_of.isoformat(),
        "account": account,
        "session_reused": session_reused,
        "strategy_profile": decision.strategy_profile,
        "strategy_cadence": decision.strategy_cadence,
        "strategy_required_inputs": list(decision.strategy_required_inputs),
        "period": decision.period,
        "policy": decision.policy,
    }
    return store.write_json(decision.state_key, payload)


def _compact_positions(payload: Any) -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []
    for row in iter_position_rows(payload):
        symbol = get_first(row, "symbol", "ticker", "security_symbol")
        if not symbol:
            continue
        positions.append(
            {
                "symbol": str(symbol).strip().upper(),
                "quantity": float_or_none(get_first(row, "quantity", "shares", "qty")),
                "market_value": float_or_none(
                    get_first(row, "market_value", "marketValue", "value", "current_value")
                ),
            }
        )
    return positions


def build_account_funds_snapshot(
    *,
    account: str,
    account_summaries: list[dict[str, Any]],
    balances: Mapping[str, Any],
    positions_payload: Any | None,
    session_reused: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    as_of = now or _utcnow()
    snapshot: dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "account": account,
        "session_reused": session_reused,
        "account_summaries": account_summaries,
        "balance_metrics": selected_numeric_metrics(balances, BALANCE_KEYWORDS),
    }
    if positions_payload is not None:
        snapshot["positions"] = _compact_positions(positions_payload)
    return snapshot


def _diagnostic_field_schema(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Describe bounded static balance fields without returning broker values."""
    fields: set[tuple[str, str]] = set()
    recognized_labels: set[tuple[str, str]] = set()
    unit_paths: set[str] = set()
    unknown_label_present = False
    unrecognized_fields_omitted = False
    unrecognized_field_count = 0
    visited = 0
    truncated = False

    def visit(value: Any, path: str, depth: int) -> None:
        nonlocal visited, truncated, unknown_label_present
        nonlocal unrecognized_fields_omitted, unrecognized_field_count
        if visited >= 256 or depth > 6 or len(fields) >= 64:
            truncated = True
            return
        visited += 1
        if isinstance(value, Mapping):
            if path:
                fields.add((path, "object"))
            for raw_key, child in value.items():
                if visited >= 256 or len(fields) >= 64:
                    truncated = True
                    break
                visited += 1
                key = str(raw_key)
                field_code = _DIAGNOSTIC_SCHEMA_KEY_CODES.get(key.casefold())
                if field_code is None:
                    unrecognized_fields_omitted = True
                    unrecognized_field_count += 1
                    continue
                child_path = f"{path}.{field_code}" if path else field_code
                if len(child_path) > 160:
                    truncated = True
                    continue
                normalized_key = field_code
                if normalized_key in _DIAGNOSTIC_UNIT_KEYS and child not in (None, ""):
                    unit_paths.add(child_path)
                if (
                    normalized_key in _DIAGNOSTIC_LABEL_KEYS
                    and isinstance(child, str)
                    and child.strip()
                ):
                    label = _DIAGNOSTIC_LABELS.get(child.strip())
                    if label:
                        recognized_labels.add((child_path, label))
                    else:
                        unknown_label_present = True
                label_from_key = _DIAGNOSTIC_LABELS.get(key)
                if label_from_key:
                    recognized_labels.add((child_path, label_from_key))
                visit(child, child_path, depth + 1)
            return
        if isinstance(value, list):
            if path:
                fields.add((path, "array"))
            for child in value[:8]:
                visit(child, f"{path}[]" if path else "[]", depth + 1)
            if len(value) > 8:
                truncated = True
            return
        if path:
            if value is None:
                value_type = "null"
            elif isinstance(value, bool):
                value_type = "boolean"
            elif isinstance(value, (int, float)):
                value_type = "number"
            elif isinstance(value, str):
                value_type = "string"
            else:
                value_type = "other"
            fields.add((path, value_type))

    visit(payload, "", 0)
    return {
        "fields": [
            {"path": path, "type": value_type}
            for path, value_type in sorted(fields)[:64]
        ],
        "recognized_display_labels": [
            {"path": path, "label": label}
            for path, label in sorted(recognized_labels)[:16]
        ],
        "unknown_display_label_present": unknown_label_present,
        "unit_field_paths": sorted(unit_paths)[:16],
        "unrecognized_field_count": unrecognized_field_count,
        "unrecognized_fields_omitted": unrecognized_fields_omitted,
        "schema_truncated": truncated or len(fields) > 64,
    }


def run_cached_balance_field_diagnostic(
    *,
    runtime_target: object,
    credentials: FirstradeCredentials | None = None,
    client_factory: Callable[..., FirstradeBrokerClient] = FirstradeBrokerClient,
    env_reader: Callable[[str, str | None], str | None] = os.getenv,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Inspect one exact RuntimeTarget account via a fresh cached-only session."""
    selectors = getattr(runtime_target, "account_selector", None)
    if (
        not isinstance(selectors, (tuple, list))
        or len(selectors) != 1
        or not isinstance(selectors[0], str)
        or not selectors[0].strip()
    ):
        return {"status": "runtime_target_invalid", "cached_session_fresh": False}
    expected_account = selectors[0].strip()
    client = None
    cached_session_fresh = False
    read_only_session_accepted = False
    try:
        resolved_credentials = credentials or FirstradeCredentials.from_env(
            env_reader, include_login_credentials=False
        )
        if not resolved_credentials.reuse_session:
            return {"status": "cached_session_not_enabled", "cached_session_fresh": False}
        client = client_factory(resolved_credentials, live_trading_enabled=False)
        cached_session_fresh = bool(client.has_fresh_cached_session())
        if not cached_session_fresh:
            return {"status": "cached_session_missing_or_stale", "cached_session_fresh": False}
        client.connect_read_only()
        if getattr(client, "session_reused", False) is not True:
            return {
                "status": "cached_session_unavailable",
                "cached_session_fresh": True,
                "read_only_session_accepted": False,
            }
        read_only_session_accepted = True
        account_numbers = client.account_numbers()
        if (
            not isinstance(account_numbers, (tuple, list))
            or sum(account == expected_account for account in account_numbers) != 1
        ):
            return {
                "status": "runtime_target_account_mismatch",
                "cached_session_fresh": True,
                "read_only_session_accepted": True,
                "account_match": False,
            }
        if client.select_account(expected_account) != expected_account:
            return {
                "status": "runtime_target_account_mismatch",
                "cached_session_fresh": True,
                "read_only_session_accepted": True,
                "account_match": False,
            }
        _, account_data = client.require_connected()
        get_account_balances = getattr(account_data, "get_account_balances", None)
        if not callable(get_account_balances):
            return {
                "status": "balance_payload_unavailable",
                "cached_session_fresh": True,
                "read_only_session_accepted": True,
                "account_match": True,
            }
        balance_payload = get_account_balances(expected_account)
        if not isinstance(balance_payload, Mapping):
            return {
                "status": "balance_payload_unavailable",
                "cached_session_fresh": True,
                "read_only_session_accepted": True,
                "account_match": True,
            }
        result = {
            "status": "ok",
            "observed_at": (now or _utcnow()).isoformat(),
            "cached_session_fresh": True,
            "read_only_session_accepted": True,
            "account_match": True,
            "source": "firstrade_private_balances",
        }
        result.update(_diagnostic_field_schema(balance_payload))
        return result
    except Exception:
        return {
        "status": "cached_read_only_unavailable",
            "cached_session_fresh": cached_session_fresh,
            "read_only_session_accepted": read_only_session_accepted,
        }
    finally:
        if client is not None:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


_ACCOUNT_SESSION_REFRESH_STATUSES = frozenset(
    {
        "runtime_target_invalid",
        "configured_account_conflict",
        "credentials_unavailable",
        "session_persistence_not_enabled",
        "mfa_required",
        "session_authentication_failed",
        "runtime_target_account_mismatch",
        "session_cache_persist_failed",
        "session_cache_readback_failed",
        "ok",
    }
)


def run_target_bound_account_session_refresh(
    *,
    runtime_target: object,
    credentials: FirstradeCredentials | None = None,
    client_factory: Callable[..., FirstradeBrokerClient] = FirstradeBrokerClient,
    env_reader: Callable[[str, str | None], str | None] = os.getenv,
) -> dict[str, Any]:
    """Authenticate once for the exact RuntimeTarget, then persist only after native identity match."""
    selectors = getattr(runtime_target, "account_selector", None)
    if (
        not isinstance(selectors, (tuple, list))
        or len(selectors) != 1
        or not isinstance(selectors[0], str)
        or not selectors[0].strip()
    ):
        return _account_session_refresh_result("runtime_target_invalid")
    expected_account = selectors[0].strip()
    configured_account = (env_reader("FIRSTRADE_ACCOUNT", "") or "").strip()
    if configured_account and configured_account != expected_account:
        return _account_session_refresh_result("configured_account_conflict")

    try:
        resolved_credentials = credentials or FirstradeCredentials.from_env(env_reader)
        resolved_credentials.require_login_fields()
    except Exception:
        return _account_session_refresh_result("credentials_unavailable")
    if (
        not resolved_credentials.reuse_session
        or not resolved_credentials.persist_session_cache
        or not resolved_credentials.gcs_state_bucket.strip()
    ):
        return _account_session_refresh_result("session_persistence_not_enabled")

    client = None
    try:
        client = client_factory(
            replace(
                resolved_credentials,
                debug=False,
                email=resolved_credentials.email if resolved_credentials.mfa_code else "",
                phone=resolved_credentials.phone if resolved_credentials.mfa_code else "",
            ),
            live_trading_enabled=False,
        )
        try:
            client.connect(defer_session_cache=True)
        except FirstradeMfaRequired:
            return _account_session_refresh_result("mfa_required")
        except Exception:
            return _account_session_refresh_result("session_authentication_failed")

        try:
            account_numbers = client.account_numbers()
            if (
                not isinstance(account_numbers, (tuple, list))
                or len(account_numbers) != 1
                or account_numbers[0] != expected_account
            ):
                return _account_session_refresh_result(
                    "runtime_target_account_mismatch", session_connected=True
                )
            if client.select_account(expected_account) != expected_account:
                return _account_session_refresh_result(
                    "runtime_target_account_mismatch", session_connected=True
                )
        except Exception:
            return _account_session_refresh_result(
                "runtime_target_account_mismatch", session_connected=True
            )

        try:
            cache_status = client.persist_session_cache_and_verify()
        except Exception:
            cache_status = "cache_persist_failed"
        if cache_status not in {"ok", "cache_persist_failed", "cache_readback_failed"}:
            cache_status = "cache_persist_failed"
        external_cache_status = {
            "cache_persist_failed": "session_cache_persist_failed",
            "cache_readback_failed": "session_cache_readback_failed",
        }.get(cache_status, cache_status)
        return _account_session_refresh_result(
            external_cache_status,
            session_connected=True,
            account_match=True,
            cache_persisted=cache_status in {"ok", "cache_readback_failed"},
            cache_readback_fresh=cache_status == "ok",
        )
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _account_session_refresh_result(
    status: str,
    *,
    session_connected: bool = False,
    account_match: bool = False,
    cache_persisted: bool = False,
    cache_readback_fresh: bool = False,
) -> dict[str, Any]:
    if status not in _ACCOUNT_SESSION_REFRESH_STATUSES:
        status = "session_authentication_failed"
    return {
        "status": status,
        "runtime_target_valid": status != "runtime_target_invalid",
        "credentials_available": status not in {
            "runtime_target_invalid",
            "configured_account_conflict",
            "credentials_unavailable",
        },
        "session_connected": session_connected,
        "account_match": account_match,
        "cache_persisted": cache_persisted,
        "cache_readback_fresh": cache_readback_fresh,
    }


def persist_funds_snapshot(
    *,
    store: GcsStateStore,
    snapshot: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    as_of = now or _utcnow()
    account_key = _safe_key(str(snapshot.get("account") or "unknown"))
    stamp = as_of.strftime("%Y%m%dT%H%M%SZ")
    store.write_json(f"accounts/{account_key}/funds/latest.json", snapshot)
    store.write_json(f"accounts/{account_key}/funds/history/{as_of:%Y/%m/%d}/{stamp}.json", snapshot)
    return True


def run_session_check(
    *,
    credentials: FirstradeCredentials | None = None,
    client_factory: Callable[..., FirstradeBrokerClient] = FirstradeBrokerClient,
    state_store: GcsStateStore | None = None,
    env_reader: Callable[[str, str | None], str | None] = os.getenv,
    now: datetime | None = None,
) -> dict[str, Any]:
    as_of = now or _utcnow()
    store = state_store or build_gcs_state_store_from_env(env_reader)
    maintenance_decision = resolve_session_check_maintenance_decision(
        state_store=store,
        env_reader=env_reader,
        now=as_of,
    )
    if not maintenance_decision.should_run:
        print(
            "Firstrade session-check skipped "
            f"policy={maintenance_decision.policy} "
            f"strategy={maintenance_decision.strategy_profile or '<unknown>'} "
            f"period={maintenance_decision.period or '<none>'} "
            f"reason={maintenance_decision.reason}",
            flush=True,
        )
        return {
            "ok": True,
            "api_kind": "unofficial-reverse-engineered",
            "session_check_skipped": True,
            **maintenance_decision.to_response_fields(),
        }

    resolved_credentials = credentials or FirstradeCredentials.from_env(env_reader)
    client = client_factory(
        resolved_credentials,
        live_trading_enabled=is_live_trading_enabled(env_reader),
    ).connect()
    session_reused = bool(getattr(client, "session_reused", False))
    account = client.select_account(env_reader("FIRSTRADE_ACCOUNT", "") or None)
    account_summaries = client.list_account_summaries()
    balances = client.get_balances(account)
    positions_payload = (
        client.get_positions(account)
        if _flag("FIRSTRADE_SESSION_CHECK_INCLUDE_POSITIONS", "false", env_reader)
        else None
    )
    snapshot = build_account_funds_snapshot(
        account=account,
        account_summaries=account_summaries,
        balances=balances,
        positions_payload=positions_payload,
        session_reused=session_reused,
        now=as_of,
    )
    snapshot_persisted = False
    snapshot_error = None
    if store is not None and _flag("FIRSTRADE_PERSIST_ACCOUNT_SNAPSHOT", "false", env_reader):
        try:
            snapshot_persisted = persist_funds_snapshot(
                store=store,
                snapshot=snapshot,
                now=as_of,
            )
        except Exception as exc:
            snapshot_error = f"{type(exc).__name__}: {exc}"
    maintenance_state_persisted = False
    maintenance_state_error = None
    if store is not None and maintenance_decision.state_key:
        try:
            maintenance_state_persisted = persist_session_check_maintenance(
                store=store,
                decision=maintenance_decision,
                account=account,
                session_reused=session_reused,
                now=as_of,
            )
        except Exception as exc:
            maintenance_state_error = f"{type(exc).__name__}: {exc}"

    print(
        "Firstrade session-check "
        f"session_reused={session_reused} "
        f"account={mask_account_id(account)} "
        f"snapshot_persisted={snapshot_persisted} "
        f"maintenance_state_persisted={maintenance_state_persisted}",
        flush=True,
    )
    result: dict[str, Any] = {
        "ok": True,
        "api_kind": "unofficial-reverse-engineered",
        "account": account,
        "session_reused": session_reused,
        "funds_snapshot": snapshot,
        "snapshot_persisted": snapshot_persisted,
        "session_check_maintenance_state_persisted": maintenance_state_persisted,
        **maintenance_decision.to_response_fields(),
    }
    if snapshot_error:
        result["snapshot_error"] = snapshot_error
    if maintenance_state_error:
        result["session_check_maintenance_state_error"] = maintenance_state_error
    return result
