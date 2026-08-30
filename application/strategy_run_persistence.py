"""Persistence helpers for strategy execution runs."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from typing import Any

from application.state_persistence import GcsStateStore
from quant_platform_kit.common.execution_outcomes import (
    DEFAULT_TERMINAL_STRATEGY_RUN_STAGES,
    STAGE_NO_ACTION,
    is_terminal_strategy_run_stage,
)

LIVE_TERMINAL_STAGES = frozenset(
    {
        *DEFAULT_TERMINAL_STRATEGY_RUN_STAGES,
        STAGE_NO_ACTION,
    }
)
LIVE_SUBMISSION_CLAIM_BOUNDARY = "pre_broker_request_v2"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def safe_key(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(value or "")) or "unknown"


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "isoformat") and callable(value.isoformat):
        try:
            return value.isoformat()
        except Exception:
            pass
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return str(value)


def _coerce_month_period(value: Any) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        text = str(value).strip()
        if len(text) >= 7 and text[4] == "-":
            return text[:7]
        return None
    return f"{parsed.year:04d}-{parsed.month:02d}"


def resolve_strategy_run_period(
    *,
    now: datetime,
    plan: Mapping[str, Any] | None = None,
    evaluation_metadata: Mapping[str, Any] | None = None,
) -> str:
    execution = dict((plan or {}).get("execution") or {})
    candidates = (
        (evaluation_metadata or {}).get("snapshot_as_of"),
        execution.get("signal_date"),
        execution.get("effective_date"),
        now,
    )
    for candidate in candidates:
        period = _coerce_month_period(candidate)
        if period:
            return period
    return f"{now.year:04d}-{now.month:02d}"


def strategy_run_state_key(
    *,
    account: str,
    strategy_profile: str,
    run_period: str,
    dry_run_only: bool = False,
) -> str:
    prefix = "strategy-runs/dry-run" if dry_run_only else "strategy-runs"
    return (
        f"{prefix}/{safe_key(account)}/{safe_key(strategy_profile)}/"
        f"{safe_key(run_period)}/latest.json"
    )


def strategy_run_history_key(
    *,
    account: str,
    strategy_profile: str,
    run_period: str,
    stage: str,
    now: datetime,
    dry_run_only: bool = False,
) -> str:
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    prefix = "strategy-runs/dry-run" if dry_run_only else "strategy-runs"
    return (
        f"{prefix}/{safe_key(account)}/{safe_key(strategy_profile)}/"
        f"{safe_key(run_period)}/history/{now:%Y/%m/%d}/{stamp}-{safe_key(stage)}.json"
    )


def strategy_run_claim_key(
    *, account: str, strategy_profile: str, run_period: str,
) -> str:
    """Permanent live claim key; a failed/unknown submission must remain blocked."""
    return (
        f"strategy-runs/claims/{safe_key(account)}/{safe_key(strategy_profile)}/"
        f"{safe_key(run_period)}.json"
    )


def claim_live_strategy_run(
    *, store: GcsStateStore, account: str, strategy_profile: str,
    run_period: str, now: datetime | None = None,
) -> bool:
    """Acquire the durable pre-order claim using object-store create-if-absent."""
    payload = {
        "stage": "PENDING_SUBMISSION",
        "submission_claim_boundary": LIVE_SUBMISSION_CLAIM_BOUNDARY,
        "account": account,
        "strategy_profile": strategy_profile,
        "run_period": run_period,
        "as_of": (now or utcnow()).isoformat(),
        "no_order_submitted": True,
    }
    return store.create_json(
        strategy_run_claim_key(
            account=account, strategy_profile=strategy_profile, run_period=run_period,
        ),
        payload,
    )


def read_live_strategy_run_claim(
    *,
    store: GcsStateStore,
    account: str,
    strategy_profile: str,
    run_period: str,
) -> dict[str, Any] | None:
    return store.read_json(
        strategy_run_claim_key(
            account=account,
            strategy_profile=strategy_profile,
            run_period=run_period,
        )
    )


def has_effective_live_submission_claim(claim: Mapping[str, Any] | None) -> bool:
    """Return whether a claim was created at the current pre-order boundary.

    Claims written before this boundary existed were acquired too early and do
    not prove a broker request. Keeping them non-blocking avoids carrying a
    historical no-order lock into the retryable lifecycle.
    """

    return (
        str((claim or {}).get("submission_claim_boundary") or "").strip()
        == LIVE_SUBMISSION_CLAIM_BOUNDARY
    )


def read_latest_strategy_run_state(
    *,
    store: GcsStateStore,
    account: str,
    strategy_profile: str,
    run_period: str,
) -> dict[str, Any] | None:
    return store.read_json(
        strategy_run_state_key(
            account=account,
            strategy_profile=strategy_profile,
            run_period=run_period,
        )
    )


def is_duplicate_live_run(existing_state: Mapping[str, Any] | None) -> bool:
    if not existing_state:
        return False
    if bool(existing_state.get("dry_run_only")):
        return False
    return is_terminal_strategy_run_stage(
        existing_state.get("stage"),
        terminal_stages=LIVE_TERMINAL_STAGES,
    )


def build_strategy_run_state(
    *,
    stage: str,
    account: str,
    strategy_profile: str,
    strategy_display_name: str,
    run_period: str,
    dry_run_only: bool,
    live_trading_enabled: bool,
    session_reused: bool,
    portfolio_snapshot: Mapping[str, Any] | None = None,
    evaluation_metadata: Mapping[str, Any] | None = None,
    plan: Mapping[str, Any] | None = None,
    submitted_orders: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    skipped_orders: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    execution_notes: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    action_done: bool = False,
    broker_submission_done: bool = False,
    execution_status: str = "",
    orders_pending_count: int = 0,
    error: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    as_of = now or utcnow()
    payload = {
        "stage": stage,
        "as_of": as_of.isoformat(),
        "account": account,
        "strategy_profile": strategy_profile,
        "strategy_display_name": strategy_display_name,
        "run_period": run_period,
        "dry_run_only": dry_run_only,
        "live_trading_enabled": live_trading_enabled,
        "session_reused": session_reused,
        "portfolio_snapshot": dict(portfolio_snapshot or {}),
        "evaluation_metadata": dict(evaluation_metadata or {}),
        "plan": dict(plan or {}),
        "submitted_orders": list(submitted_orders),
        "skipped_orders": list(skipped_orders),
        "execution_notes": list(execution_notes),
        "action_done": action_done,
        "broker_submission_done": broker_submission_done,
        "execution_status": str(execution_status or ""),
        "orders_pending_count": max(0, int(orders_pending_count or 0)),
    }
    if error:
        payload["error"] = error
    return _jsonable(payload)


def persist_strategy_run_state(
    *,
    store: GcsStateStore,
    state: Mapping[str, Any],
    now: datetime | None = None,
) -> bool:
    as_of = now or utcnow()
    account = str(state.get("account") or "unknown")
    strategy_profile = str(state.get("strategy_profile") or "unknown")
    run_period = str(state.get("run_period") or f"{as_of.year:04d}-{as_of.month:02d}")
    stage = str(state.get("stage") or "UNKNOWN")
    dry_run_only = bool(state.get("dry_run_only"))
    store.write_json(
        strategy_run_state_key(
            account=account,
            strategy_profile=strategy_profile,
            run_period=run_period,
            dry_run_only=dry_run_only,
        ),
        dict(state),
    )
    store.write_json(
        strategy_run_history_key(
            account=account,
            strategy_profile=strategy_profile,
            run_period=run_period,
            stage=stage,
            now=as_of,
            dry_run_only=dry_run_only,
        ),
        dict(state),
    )
    return True
