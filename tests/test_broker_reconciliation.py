from __future__ import annotations

from datetime import datetime, timezone

import pytest

from application.broker_reconciliation import (
    FirstradeReconciliationUnavailable,
    collect_broker_reconciliation_evidence,
)
from quant_platform_kit.common.broker_reconciliation import build_broker_reconciliation_evidence


def _evidence():
    return build_broker_reconciliation_evidence(
        platform_id="firstrade",
        strategy_profile="sample_profile",
        account_scope_sha256="1" * 64,
        baseline_id="firstrade-baseline-001",
        baseline_target_sha256="2" * 64,
        runtime_target_sha256="2" * 64,
        observed_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        broker_connected=True,
        account_identity_match=True,
        positions_match=True,
        cash_match=True,
        open_orders_match=True,
        recent_executions_match=True,
        local_execution_ledger_match=True,
        positions_sha256="3" * 64,
        cash_sha256="4" * 64,
        open_orders_sha256="5" * 64,
        recent_executions_sha256="6" * 64,
        local_execution_ledger_sha256="7" * 64,
    )


def test_reconciliation_entrypoint_fails_closed_before_collector_is_invoked():
    calls = 0

    def collector():
        nonlocal calls
        calls += 1
        return _evidence()

    with pytest.raises(FirstradeReconciliationUnavailable, match="not configured"):
        collect_broker_reconciliation_evidence()

    assert calls == 0


def test_reconciliation_entrypoint_returns_only_qpk_evidence():
    evidence = _evidence()

    assert collect_broker_reconciliation_evidence(collector=lambda: evidence) is evidence


def test_reconciliation_entrypoint_rejects_non_qpk_collector_result():
    with pytest.raises(FirstradeReconciliationUnavailable, match="QPK evidence"):
        collect_broker_reconciliation_evidence(collector=lambda: object())
