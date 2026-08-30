"""Bounded execution-receipt facts derived from Firstrade cycle results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from quant_platform_kit.common.execution_receipts import (
    attach_runtime_execution_receipt,
    resolve_execution_receipt_fact,
)


_RECONCILIATION_STAGES = frozenset({"PENDING_RECONCILIATION", "PENDING_SUBMISSION"})
_RISK_BLOCKED_STAGES = frozenset({"EXECUTION_BLOCKED", "FUNDING_BLOCKED"})


def attach_strategy_result_execution_receipt(
    report: dict[str, Any],
    result: Mapping[str, Any],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Attach only the highest broker fact already present in a cycle result.

    A locally recorded submission is deliberately reported as ``submitted``;
    Firstrade's cycle result has no fill-confirmation field, so this adapter
    never infers an acknowledgement or fill from it.
    """

    stage = str(result.get("strategy_run_stage") or "").strip().upper()
    submitted_orders = _as_sequence(result.get("submitted_orders"))
    submission_attempted = bool(
        result.get("action_done")
        or result.get("broker_submission_done")
        or submitted_orders
    )
    reconciliation_required = stage in _RECONCILIATION_STAGES or bool(
        result.get("execution_status") == "pending_reconciliation"
        or result.get("orders_pending_count")
    )
    risk_blocked = not submission_attempted and (
        stage in _RISK_BLOCKED_STAGES
        or bool(result.get("execution_blocked"))
        or bool(result.get("funding_blocked"))
    )
    failed = (
        result.get("ok", True) is False
        and not submission_attempted
        and not reconciliation_required
        and not risk_blocked
    )
    outcome, confirmation = resolve_execution_receipt_fact(
        dry_run=dry_run,
        submission_attempted=submission_attempted,
        reconciliation_required=reconciliation_required,
        risk_blocked=risk_blocked,
        failed=failed,
    )
    return attach_runtime_execution_receipt(
        report,
        outcome=outcome,
        broker_confirmation=confirmation,
    )


def attach_unknown_failure_execution_receipt(report: dict[str, Any]) -> dict[str, Any]:
    """Preserve uncertainty when an exception escapes the strategy cycle."""

    outcome, confirmation = resolve_execution_receipt_fact(
        dry_run=bool(report.get("dry_run")),
        submission_attempted=True,
        failed=True,
    )
    return attach_runtime_execution_receipt(
        report,
        outcome=outcome,
        broker_confirmation=confirmation,
    )


def _as_sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, (list, tuple)) else ()
