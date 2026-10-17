"""Offline candidate/incident integration tests with durable fake CAS storage."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from notifications import unified
from notifications.digest_snapshot.digest_candidates import cycle_evidence

NOW = datetime(2026, 10, 17, 0, tzinfo=timezone.utc)
TARGET = dict(platform_id="firstrade", strategy_profile="synthetic", service_name="synthetic-service",
              account_scope="synthetic-scope", account_selector=["synthetic-selector"],
              deployment_selector="synthetic-slot", dry_run_only=False)
ENV = {unified.FLAG: "true", "RUNTIME_TARGET_ENABLED": "true", "RUNTIME_TARGET_JSON": json.dumps(TARGET),
       "DIGEST_STORE_TARGET": "synthetic", "DIGEST_OPAQUE_ACCOUNT_UID": "opaque-synthetic",
       "DIGEST_TARGET_ID": "target-synthetic", "DIGEST_CANDIDATES_GCS_URI": "gs://synthetic/candidates",
       "EXECUTION_REPORT_GCS_URI": "gs://synthetic/reports"}


class Store:
    def __init__(self):
        self.data = {}
        self.calls = []
        self.fail_candidates = False
        self.fail_reads = False

    def read(self, key):
        self.calls.append(("read", key))
        if self.fail_reads:
            raise RuntimeError("unreadable_history")
        return deepcopy(self.data.get(key, (None, None)))

    def compare_and_set(self, key, version, value):
        self.calls.append(("cas", key))
        if self.fail_candidates and "/_notification_markers/" not in key:
            raise RuntimeError("uncertain_write")
        old_version = self.data.get(key, (None, None))[0]
        if old_version != version:
            return False
        self.data[key] = ((old_version or 0) + 1, deepcopy(value))
        return True


def source(run_id="synthetic-run", *, status="no_action", orders=(), **changes):
    result = dict(ok=True, execution_status=status, submitted_orders=list(orders), orders_pending_count=0,
                  dry_run_only=False)
    result.update(changes)
    report = dict(schema_version="runtime_report.v1", runtime_target=deepcopy(TARGET), platform="firstrade",
        strategy_profile="synthetic", service_name="synthetic-service", run_id=run_id,
        started_at=(NOW - timedelta(hours=4)).isoformat(), finished_at=(NOW - timedelta(hours=3)).isoformat(),
        status="ok" if result["ok"] else "error", dry_run=False,
        summary={"notification_digest": cycle_evidence(result, orders)})
    return report, result


def notify(store, report, result, sent, *, now=NOW, env=ENV):
    def send():
        store.calls.append(("send",))
        sent.append(result["execution_status"])
        return True
    return unified.notify_cycle(report, result, settings=None, send=send, env=env, now=now, store=store)


def candidate(store):
    return next(value for _, value in store.data.values() if value.get("schema_version") == "qsl.digest_candidates.v1")


@pytest.mark.parametrize("value,expected", [(None, False), ("", False), ("invalid", False),
    ("1", False), ("false", False), (" TRUE ", True), (True, True)])
def test_strict_release_flag(value, expected):
    assert unified.enabled({unified.FLAG: value}) is expected


def test_routine_persists_before_deferral_and_same_run_is_counted_once():
    store, sent = Store(), []
    report, result = source()
    original = deepcopy((report, result))
    assert notify(store, report, result, sent)["route"] == "digest_candidate"
    assert notify(store, report, result, sent)["route"] == "digest_candidate"
    payload = candidate(store)
    assert sent == [] and len(payload["runs"][0]["notification_events"]) == 1
    assert payload["runs"][0]["cycle_count"] == 1
    assert (payload["runs"][0]["order_count"], payload["runs"][0]["fill_count"]) == (0, 0)
    assert (report, result) == original


def test_candidate_rows_keep_each_runs_counts_and_events_separate():
    store, sent = Store(), []
    zero, zero_result = source("zero-run")
    order, order_result = source("order-run", status="completed", orders=[{
        "status": "filled", "broker_order_id": "synthetic-order",
        "fills": [{"execution_id": "synthetic-fill"}],
    }])
    for report, result in ((zero, zero_result), (order, order_result), (zero, zero_result)):
        notify(store, report, result, sent)
    rows = {row["source_run_id"]: row for row in candidate(store)["runs"]}
    assert "notification_source_runs" not in candidate(store)
    assert "notification_events" not in candidate(store)
    assert set(rows) == {"zero-run", "order-run"}
    assert [(rows[key]["order_count"], rows[key]["fill_count"], rows[key]["cycle_count"])
            for key in ("zero-run", "order-run")] == [(0, 0, 1), (1, 1, 1)]
    for run_id, row in rows.items():
        assert len(row["notification_events"]) == 1
        assert {event["source_run_id"] for event in row["notification_events"]} == {run_id}
    assert len(sent) == 1


def test_former_top_level_ledger_migrates_to_per_run_rows_without_recounting():
    store, sent = Store(), []
    report, result = source()
    notify(store, report, result, sent)
    uri = next(key for key in store.data if "/_notification_markers/" not in key)
    version, payload = store.data[uri]
    row = payload["runs"][0]
    events = row.pop("notification_events")
    row.pop("source_run_id")
    store.data[uri] = (version, {"schema_version": "qsl.digest_candidates.v1", "runs": [row],
        "notification_source_runs": {report["run_id"]: row},
        "notification_events": {event["dedupe_key"]: event for event in events}})
    notify(store, report, result, sent)
    row = candidate(store)["runs"][0]
    assert row["source_run_id"] == report["run_id"] and row["cycle_count"] == 1
    assert row["order_count"] == 0 and len(row["notification_events"]) == 1
    assert set(candidate(store)) == {"schema_version", "producer_status", "runs"}


@pytest.mark.parametrize("outcome,expected,ack", [
    (True, "uncertain", True),
    ({"acknowledged": True}, "uncertain", True),
    ({"acknowledged": True, "receipt_id": "  "}, "uncertain", True),
    (False, "uncertain", False),
    ({"acknowledged": True, "receipt_id": "telegram:synthetic-message"}, "delivered", True),
])
def test_delivery_requires_sender_receipt_and_uncertain_is_not_resent(outcome, expected, ack):
    store, sends = Store(), []
    report, result = source(status="blocked", execution_blocked=True)
    def send():
        sends.append(True)
        return outcome
    first = unified.notify_cycle(report, result, settings=None, send=send, env=ENV, now=NOW, store=store)
    assert first["delivery_status"] == expected
    assert first["transport_acknowledged"] is ack
    markers = [value for key, (_, value) in store.data.items() if "/_notification_markers/" in key]
    assert len(markers) == 1 and markers[0]["delivery_status"] == expected
    assert markers[0]["receipt_id"] == (outcome["receipt_id"] if expected == "delivered" else None)
    second = unified.notify_cycle(report, result, settings=None, send=send, env=ENV, now=NOW, store=store)
    assert second["delivery_status"] == "duplicate" and len(sends) == 1


def test_real_orders_remain_immediate_and_unknown_fills_stay_null():
    store, sent = Store(), []
    report, result = source(status="completed", orders=[{"status": "filled", "broker_order_id": "synthetic-order"}])
    assert notify(store, report, result, sent)["transport_acknowledged"] is True
    assert len(sent) == 1
    assert notify(store, report, result, sent)["delivery_status"] == "duplicate"
    assert len(sent) == 1
    row = candidate(store)["runs"][0]
    assert row["order_count"] == 1 and row["fill_count"] is None
    assert next(k for k in store.calls if k[0] == "cas")
    assert store.calls.index(("send",)) > next(i for i, c in enumerate(store.calls)
        if c[0] == "cas" and "/_notification_markers/" in c[1])


@pytest.mark.parametrize("status,changes,kind", [
    ("blocked", {"execution_blocked": True}, "blocked_run"),
    ("pending", {"orders_pending_count": 1}, "order_status_unknown"),
    ("failed", {"auth_expired": True}, "auth_failure"),
    ("failed", {"ok": False}, "real_error"),
    ("completed", {"strategy_run_persistence_error": "synthetic"}, "real_error"),
])
def test_alerts_stay_immediate_across_runs_and_instances(status, changes, kind):
    store, sent = Store(), []
    report, result = source(status=status, **changes)
    assert unified.cycle_kind(result) == kind
    assert notify(store, report, result, sent)["transport_acknowledged"] is True
    second, _ = source("other-run", status=status, **changes)
    assert notify(store, second, result, sent, now=NOW + timedelta(minutes=1))["delivery_status"] == "duplicate"
    assert len(sent) == 1
    assert candidate(store)["runs"][0]["status"] == "alert"


def test_proven_recovery_then_reworsening_and_severity_increase_are_new_events():
    store, sent = Store(), []
    bad, bad_result = source(status="blocked", execution_blocked=True)
    notify(store, bad, bad_result, sent)
    good, good_result = source("recovered")
    good["started_at"] = NOW.isoformat()
    notify(store, good, good_result, sent, now=NOW + timedelta(minutes=1))
    worse, _ = source("reworsened", status="blocked", execution_blocked=True)
    worse["started_at"] = (NOW + timedelta(minutes=2)).isoformat()
    assert notify(store, worse, bad_result, sent, now=NOW + timedelta(minutes=3))["transport_acknowledged"] is True
    assert len(sent) == 2
    risk, risk_result = source("risk", notification_attention_reason_codes=["synthetic"], risk_level="ACTION")
    notify(store, risk, risk_result, sent, now=NOW + timedelta(minutes=4))
    risk_result["risk_level"] = "HALT"
    notify(store, risk, risk_result, sent, now=NOW + timedelta(minutes=5))
    assert len(sent) == 4


def test_candidate_failure_preserves_routine_send_but_marker_failure_never_blind_sends():
    store, sent = Store(), []
    store.fail_candidates = True
    report, result = source()
    assert notify(store, report, result, sent)["route"] == "immediate_fallback"
    assert len(sent) == 1
    store.fail_reads = True
    report, result = source(status="blocked", execution_blocked=True)
    assert notify(store, report, result, sent)["route"] == "failed"
    assert len(sent) == 1


def test_uncertain_ack_and_out_of_order_observation_do_not_resend():
    store = Store()
    report, result = source(status="blocked", execution_blocked=True)
    sends = []
    def send():
        sends.append(True)
        return False
    first = unified.notify_cycle(report, result, settings=None, send=send, env=ENV, now=NOW, store=store)
    assert first["delivery_status"] == "uncertain"
    unified.notify_cycle(report, result, settings=None, send=send, env=ENV, now=NOW, store=store)
    assert len(sends) == 1
    old, old_result = source("old-recovery")
    assert notify(store, old, old_result, [], now=NOW - timedelta(minutes=1))["route"] == "digest_candidate"
    assert notify(store, report, result, [], now=NOW + timedelta(minutes=1))["delivery_status"] == "duplicate"


def archive_read(reports):
    def read(args):
        if args[0] == "ls":
            return [{"url": f"gs://synthetic/reports/firstrade/synthetic/2026-10/{i}.json",
                "metadata": {"updated": report["finished_at"]}} for i, report in enumerate(reports)]
        return deepcopy(reports[int(args[1].rsplit("/", 1)[1].split(".")[0])])
    return read


def test_runtime_and_archive_share_source_run_dedupe_and_do_not_erase_alerts():
    store, sent = Store(), []
    report, result = source()
    notify(store, report, result, sent)
    out = unified.emit_candidates(ENV, now=NOW, store=store, read=archive_read([report]))
    assert out["uploaded"] is True
    assert candidate(store)["runs"][0]["cycle_count"] == 1
    assert len(candidate(store)["runs"]) == 1
    changed = deepcopy(report)
    changed["status"] = "error"
    unified.emit_candidates(ENV, now=NOW, store=store, read=archive_read([changed]))
    assert candidate(store)["runs"][0]["status"] == "alert"
    assert candidate(store)["runs"][0]["order_count"] is None


def test_heartbeat_missing_receipt_is_durable_and_success_recovers_exact_target():
    store, sent = Store(), []
    report, _ = source()
    def call(kind, now, reports=()):
        return unified.notify_heartbeat(kind=kind, send=lambda: sent.append(kind) or True, now=now,
            env=ENV, store=store, read=archive_read([report]), recovery_reports=reports)
    call("receipt_missing", NOW)
    call("receipt_missing", NOW + timedelta(minutes=1))
    assert sent == ["receipt_missing"]
    call("healthy_heartbeat", NOW + timedelta(minutes=2), [report])
    call("receipt_missing", NOW + timedelta(minutes=3))
    assert sent == ["receipt_missing", "receipt_missing"]


def test_no_runs_need_schedule_proof_and_never_invent_zero_counts(monkeypatch):
    import quant_platform_kit.common.runtime_heartbeat_policy as policy
    store, sent = Store(), []
    env = {**ENV, "DIGEST_NOTIFICATION_SCHEDULE_JSON": json.dumps({"scheduler": {"main_time": "0 20 * * 1-5"}})}
    monkeypatch.setattr(policy, "filter_due_targets", lambda *a, **kw: ([], True))
    result = unified.notify_heartbeat(kind="healthy_heartbeat", send=lambda: sent.append(True) or True,
        now=NOW, env=env, store=store, read=lambda args: [])
    assert result["candidate_persisted"] is True and sent == []
    row = candidate(store)["runs"][0]
    assert row["actually_ran"] is False and "order_count" not in row
    assert "source_run_id" not in row


def test_two_accounts_do_not_share_marker_or_candidate():
    store, sent = Store(), []
    report, result = source(status="blocked", execution_blocked=True)
    notify(store, report, result, sent)
    other = {**ENV, "DIGEST_OPAQUE_ACCOUNT_UID": "other-opaque", "DIGEST_TARGET_ID": "other-target",
             "DIGEST_STORE_TARGET": "other"}
    notify(store, report, result, sent, env=other)
    assert len(sent) == 2
    assert len([k for k in store.data if "/_notification_markers/" not in k]) == 2
