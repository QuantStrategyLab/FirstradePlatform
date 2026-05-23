"""Read-only Firstrade session and account-state checks."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
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
        "account": mask_account_id(account),
        "session_reused": session_reused,
        "account_summaries": account_summaries,
        "balance_metrics": selected_numeric_metrics(balances, BALANCE_KEYWORDS),
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
