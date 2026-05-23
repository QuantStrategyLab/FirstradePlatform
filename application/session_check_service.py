"""Read-only Firstrade session and account-state checks."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from application.firstrade_client import (
    FirstradeBrokerClient,
    FirstradeCredentials,
    is_live_trading_enabled,
    mask_account_id,
)
from application.state_persistence import GcsStateStore, build_gcs_state_store_from_env

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


def _flag(name: str, default: str = "false", env: Callable[[str, str | None], str | None] = os.getenv) -> bool:
    return (env(name, default) or "").strip().lower() == "true"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_key(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(value or "")) or "unknown"


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _flatten_values(payload: Any, prefix: str = "") -> dict[str, Any]:
    values: dict[str, Any] = {}
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            child_key = f"{prefix}.{key}" if prefix else str(key)
            values.update(_flatten_values(value, child_key))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            values.update(_flatten_values(value, f"{prefix}.{index}"))
    else:
        values[prefix] = payload
    return values


def _selected_balance_metrics(payload: Any) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for key, value in _flatten_values(payload).items():
        lowered = key.lower()
        if not any(keyword in lowered for keyword in BALANCE_KEYWORDS):
            continue
        number = _float_or_none(value)
        if number is not None:
            metrics[key] = number
    return metrics


def _iter_position_rows(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        for key in ("items", "positions", "data", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, Mapping)]
        if "symbol" in payload:
            return [payload]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, Mapping)]
    return []


def _get_first(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    lower_map = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        lowered = key.lower()
        if lowered in lower_map:
            return lower_map[lowered]
    return None


def _compact_positions(payload: Any) -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []
    for row in _iter_position_rows(payload):
        symbol = _get_first(row, "symbol", "ticker", "security_symbol")
        if not symbol:
            continue
        positions.append(
            {
                "symbol": str(symbol).strip().upper(),
                "quantity": _float_or_none(_get_first(row, "quantity", "shares", "qty")),
                "market_value": _float_or_none(
                    _get_first(row, "market_value", "marketValue", "value", "current_value")
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
        "account": mask_account_id(account),
        "session_reused": session_reused,
        "account_summaries": account_summaries,
        "balance_metrics": _selected_balance_metrics(balances),
    }
    if positions_payload is not None:
        snapshot["positions"] = _compact_positions(positions_payload)
    return snapshot


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
    resolved_credentials = credentials or FirstradeCredentials.from_env(env_reader)
    store = state_store or build_gcs_state_store_from_env(env_reader)
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
        now=now,
    )
    snapshot_persisted = False
    snapshot_error = None
    if store is not None and _flag("FIRSTRADE_PERSIST_ACCOUNT_SNAPSHOT", "false", env_reader):
        try:
            snapshot_persisted = persist_funds_snapshot(
                store=store,
                snapshot=snapshot,
                now=now,
            )
        except Exception as exc:
            snapshot_error = f"{type(exc).__name__}: {exc}"

    print(
        "Firstrade session-check "
        f"session_reused={session_reused} "
        f"account={mask_account_id(account)} "
        f"snapshot_persisted={snapshot_persisted}",
        flush=True,
    )
    result: dict[str, Any] = {
        "ok": True,
        "api_kind": "unofficial-reverse-engineered",
        "account": mask_account_id(account),
        "session_reused": session_reused,
        "funds_snapshot": snapshot,
        "snapshot_persisted": snapshot_persisted,
    }
    if snapshot_error:
        result["snapshot_error"] = snapshot_error
    return result
