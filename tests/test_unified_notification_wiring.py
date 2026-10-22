from types import SimpleNamespace
from copy import deepcopy

from application.rebalance_service import _should_publish_cycle_notification


def test_confirmed_routine_orders_defer_and_failures_stay_live(monkeypatch):
    monkeypatch.setenv("QSL_ROUTINE_NOTIFICATION_MODE", "daily_digest")
    result = {"ok": True, "execution_status": "completed", "submitted_orders": [{"status": "filled"}]}
    original = deepcopy(result)
    assert not _should_publish_cycle_notification(result)
    assert result == original
    assert _should_publish_cycle_notification({**result, "strategy_run_persistence_error": "synthetic_failure"})
    assert _should_publish_cycle_notification({**result, "pending_reconciliation": True})
    monkeypatch.setenv("QSL_DIGEST_ROUTINE_ORDERS", "false")
    assert _should_publish_cycle_notification(result)


def test_archive_only_migration_keeps_healthy_legacy_cycles_quiet_and_real_events_live(monkeypatch):
    monkeypatch.setenv("QSL_ROUTINE_NOTIFICATION_MODE", "immediate")
    monkeypatch.setenv("FIRSTRADE_UNIFIED_NOTIFICATION_ENABLED", "false")
    normal = {"ok": True, "execution_status": "no_action", "submitted_orders": []}
    assert not _should_publish_cycle_notification(normal)
    assert _should_publish_cycle_notification({**normal, "execution_status": "completed",
        "submitted_orders": [{"status": "submitted", "broker_order_id": "synthetic-order"}]})
    assert _should_publish_cycle_notification({**normal, "execution_status": "unknown"})
    assert _should_publish_cycle_notification({**normal, "error": "synthetic_error"})
