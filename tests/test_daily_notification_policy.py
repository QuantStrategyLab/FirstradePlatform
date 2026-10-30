"""Offline notification routing and archive coverage; no broker or network calls."""
import copy
import datetime as dt
import json
from types import SimpleNamespace

import pytest

from notifications import unified
from notifications.digest_snapshot.digest_candidates import cycle_evidence


class MemoryStore:
    def __init__(self):
        self.values = {}
    def read(self, key):
        version, value = self.values.get(key, (None, None))
        return version, copy.deepcopy(value)
    def compare_and_set(self, key, version, value):
        current, _ = self.values.get(key, (None, None))
        if current != version:
            return False
        self.values[key] = ((current or 0) + 1, copy.deepcopy(value))
        return True


def fixtures(*, status="accepted"):
    now = dt.datetime.now(dt.timezone.utc)
    target = {"platform_id": "firstrade", "strategy_profile": "synthetic", "service_name": "synthetic-service",
              "account_scope": "live", "account_selector": ["live"], "deployment_selector": "live", "dry_run_only": False}
    order = {"status": status, "broker_order_id": "synthetic-id"}
    result = {"ok": True, "execution_status": "pending_reconciliation", "broker_submission_done": True,
              "orders_pending_count": 1, "submitted_orders": [order], "skipped_orders": [], "dry_run_only": False}
    report = {"schema_version": "runtime_report.v1", "platform": "firstrade", "strategy_profile": "synthetic",
              "service_name": "synthetic-service", "runtime_target": target, "dry_run": False, "status": "ok",
              "started_at": now.isoformat(), "run_id": "synthetic-run", "errors": [],
              "summary": {"execution_status": "PENDING_RECONCILIATION",
                          "notification_digest": cycle_evidence(result, [order], known_pending=unified.known_pending_orders(result)),
                          "routine_observation_counts": {"watch": 1}}}
    env = {"RUNTIME_TARGET_ENABLED": "true", "RUNTIME_TARGET_JSON": json.dumps(target),
           "DIGEST_CANDIDATES_GCS_URI": "gs://synthetic/candidates", "DIGEST_STORE_TARGET": "slot",
           "DIGEST_OPAQUE_ACCOUNT_UID": "opaque", "DIGEST_TARGET_ID": "target"}
    return now, result, report, env


@pytest.mark.parametrize("flag", [None, "false", "true"])
def test_acknowledged_order_is_durable_daily_candidate_for_every_flag(flag):
    now, result, report, env = fixtures()
    if flag is not None:
        env[unified.FLAG] = flag
    store, sent = MemoryStore(), []
    outcome = unified.notify_cycle(report, result, settings=SimpleNamespace(), env=env, now=now, store=store,
        send=lambda: sent.append("normal"), send_failure=lambda: sent.append("coverage"))
    assert outcome["route"] == "digest_candidate"
    assert outcome["candidate_persisted"] is True and sent == []
    payload = next(value for _, value in store.values.values() if "runs" in value)
    row = payload["runs"][0]
    assert row["source_run_id"] == "synthetic-run"
    assert row["order_count"] == 1 and row["fill_count"] is None
    assert row["execution_status"] == "order_submitted"
    assert row["routine_observation_counts"] == {"watch": 1}


def test_unknown_or_mixed_rejected_orders_never_become_routine():
    _, result, _, _ = fixtures(status="unknown")
    assert not unified.known_pending_orders(result)
    assert unified.cycle_kind(result) == "order_status_unknown"
    _, result, _, _ = fixtures()
    result["skipped_orders"] = [{"reason": "rejected"}]
    assert not unified.known_pending_orders(result)
    assert unified.cycle_kind(result) == "order_status_unknown"
    result["skipped_orders"] = [{"reason": "below_trade_threshold"}]
    assert unified.known_pending_orders(result)
    result["skipped_orders"] = [{"reason": "pending_sell_release"}]
    assert not unified.known_pending_orders(result)
    for reason in ("insufficient_cash_for_whole_share", "buy_quantity_zero", "sell_quantity_zero"):
        result["skipped_orders"] = [{"reason": reason}]
        assert not unified.known_pending_orders(result)
    _, result, _, _ = fixtures()
    result["submitted_orders"][0]["raw_payload"] = {"error": "synthetic"}
    assert not unified.known_pending_orders(result)
    assert unified.cycle_kind(result) == "order_status_unknown"


@pytest.mark.parametrize("flag", [None, "false", "true"])
def test_unknown_broker_outcome_remains_immediate_for_every_flag(flag):
    now, result, report, env = fixtures(status="unknown")
    if flag is not None:
        env[unified.FLAG] = flag
    sent = []
    outcome = unified.notify_cycle(report, result, settings=SimpleNamespace(), env=env, now=now, store=MemoryStore(),
        send=lambda: sent.append("urgent") or True, send_failure=lambda: sent.append("coverage"))
    assert sent == ["urgent"]
    assert outcome.get("transport_acknowledged") is True


@pytest.mark.parametrize("flag", [None, "false", "true"])
def test_no_trade_cycle_is_archived_for_daily_digest(flag):
    now, result, report, env = fixtures()
    result.update(execution_status="no_action", submitted_orders=[], orders_pending_count=0, broker_submission_done=False)
    report["summary"]["execution_status"] = "no_action"
    report["summary"]["notification_digest"] = cycle_evidence(result, [])
    if flag is not None:
        env[unified.FLAG] = flag
    store, sent = MemoryStore(), []
    outcome = unified.notify_cycle(report, result, settings=SimpleNamespace(), env=env, now=now, store=store,
        send=lambda: sent.append("routine"), send_failure=lambda: sent.append("coverage"))
    assert outcome["candidate_persisted"] is True and sent == []
    payload = next(value for _, value in store.values.values() if "runs" in value)
    assert payload["runs"][0]["order_count"] == 0


@pytest.mark.parametrize("flag", [None, "false", "true"])
def test_completed_window_exports_each_archived_run_for_every_flag(flag):
    _, _, report, env = fixtures()
    now = dt.datetime(2026, 10, 30, 3, tzinfo=dt.timezone.utc)
    stamp = dt.datetime(2026, 10, 29, 20, tzinfo=dt.timezone.utc)
    report["started_at"] = stamp.isoformat()
    env["EXECUTION_REPORT_GCS_URI"] = "gs://synthetic/reports"
    if flag is not None:
        env[unified.FLAG] = flag
    uri = "gs://synthetic/reports/firstrade/synthetic/2026-10/run.json"
    def read(args):
        if args[0] == "ls":
            return [{"url": uri, "metadata": {"updated": stamp.isoformat()}}] if "/2026-10/" in args[-1] else []
        assert args == ["cat", uri]
        return report
    store = MemoryStore()
    result = unified.emit_candidates(env, now=now, read=read, store=store)
    assert result["uploaded"] is True
    payload = next(value for _, value in store.values.values() if "runs" in value)
    row = payload["runs"][0]
    assert row["source_run_id"] == "synthetic-run"
    assert row["order_count"] == 1 and row["fill_count"] is None
    assert row["routine_observation_counts"] == {"watch": 1}


@pytest.mark.parametrize("flag", [None, "false", "true"])
def test_missing_binding_sends_only_fixed_coverage_failure(flag):
    now, result, report, env = fixtures()
    env.pop("DIGEST_TARGET_ID")
    if flag is not None:
        env[unified.FLAG] = flag
    sent = []
    outcome = unified.notify_cycle(report, result, settings=SimpleNamespace(), env=env, now=now, store=MemoryStore(),
        send=lambda: sent.append("private routine body"), send_failure=lambda: sent.append("coverage error"))
    assert sent == ["coverage error"]
    assert outcome["candidate_persisted"] is False


def test_missing_failure_callback_never_sends_routine_body():
    now, result, report, env = fixtures()
    env.pop("DIGEST_TARGET_ID")
    sent = []
    outcome = unified.notify_cycle(report, result, settings=SimpleNamespace(), env=env, now=now, store=MemoryStore(),
                                   send=lambda: sent.append("private routine body"))
    assert sent == []
    assert outcome["candidate_persisted"] is False


def test_same_run_observation_conflict_never_overwrites_prior_candidate():
    now, result, report, env = fixtures()
    report["summary"]["routine_observation_counts"] = {}
    store, sent = MemoryStore(), []
    first = unified.notify_cycle(report, result, settings=SimpleNamespace(), env=env, now=now, store=store,
        send=lambda: sent.append("routine"), send_failure=lambda: sent.append("coverage") or True)
    assert first["candidate_persisted"] is True
    changed = copy.deepcopy(report)
    changed["summary"]["routine_observation_counts"] = {"watch": 1}
    second = unified.notify_cycle(changed, result, settings=SimpleNamespace(), env=env, now=now, store=store,
        send=lambda: sent.append("routine"), send_failure=lambda: sent.append("coverage") or True)
    assert second["candidate_persisted"] is False
    payload = next(value for _, value in store.values.values() if "runs" in value)
    assert payload["runs"][0]["routine_observation_counts"] == {}
    assert sent == ["coverage"]


@pytest.mark.parametrize("locale,expected", [("zh", "通知候选未能确认写入"),
                                              ("en", "Notification candidate storage could not be confirmed")])
def test_coverage_error_is_localized_without_trade_details(locale, expected):
    message = unified.alert_text("candidate_write_failure", locale)
    assert expected in message
    assert "synthetic-id" not in message


def test_routine_plugin_filter_preserves_urgent_and_counts_only_safe_routes():
    from notifications.digest_snapshot.digest_candidates import routine_observation_counts
    from notifications.digest_snapshot.digest_routing import immediate_plugin_signals
    watch = SimpleNamespace(canonical_route="watch", suggested_action="observe")
    urgent = SimpleNamespace(canonical_route="watch", suggested_action="observe", error="synthetic")
    assert immediate_plugin_signals([watch, urgent], mode="daily_digest") == (urgent,)
    assert routine_observation_counts([watch, urgent]) == {"watch": 1}


def test_archive_confirmation_requires_exact_configured_uri(monkeypatch):
    from main import _report_archive_confirmed
    from quant_platform_kit.common.runtime_reports import build_runtime_report_cloud_uri
    monkeypatch.setenv("EXECUTION_REPORT_GCS_URI", "gs://synthetic/reports")
    monkeypatch.delenv("QSL_EXECUTION_REPORT_GCS_URI", raising=False)
    report = {"platform": "firstrade", "strategy_profile": "synthetic", "account_scope": "live",
              "started_at": "2026-10-29T20:00:00Z", "run_id": "synthetic-run"}
    uri = build_runtime_report_cloud_uri(report, cloud_prefix_uri="gs://synthetic/reports")
    report["artifacts"] = {"runtime_report_cloud_uri": uri}
    assert _report_archive_confirmed(report, uri)
    assert not _report_archive_confirmed(report, "gs://other/reports/fake.json")
    assert not _report_archive_confirmed(report, "/tmp/report.json")
    report["artifacts"]["runtime_report_cloud_uri"] = "gs://synthetic/reports/wrong.json"
    assert not _report_archive_confirmed(report, uri)


def test_direct_urgent_with_watch_reports_coverage_without_suppressing_urgent(monkeypatch):
    from application.rebalance_service import _report_unarchived_direct_coverage
    settings = SimpleNamespace(notify_lang="en", tg_token=None, tg_chat_id=None)
    monkeypatch.setenv("QSL_NOTIFY_LANG", " EN-us ")
    result = {"ok": False, "error": "synthetic", "routine_observation_counts": {"watch": 1}}
    sent = []
    assert not _report_unarchived_direct_coverage(result, settings=settings, notification_sender=sent.append)
    assert sent == [unified.alert_text("candidate_write_failure", "en")]
    assert result["error"] == "synthetic"
    sent.clear()
    result = {"ok": False, "error": "synthetic", "routine_observation_counts": {}}
    assert not _report_unarchived_direct_coverage(result, settings=settings, notification_sender=sent.append)
    assert not sent


def test_direct_normal_preview_does_not_send_coverage():
    from application.rebalance_service import _report_unarchived_direct_coverage
    result = {"ok": True, "dry_run_only": True, "execution_status": "dry_run",
              "routine_observation_counts": {"watch": 1}}
    sent = []
    assert _report_unarchived_direct_coverage(result, settings=SimpleNamespace(), notification_sender=sent.append)
    assert not sent
    assert "notification_digest_coverage_error" not in result


def test_main_normal_preview_without_archive_does_not_send(monkeypatch):
    import main
    settings = SimpleNamespace(dry_run_only=True, tg_token="synthetic", tg_chat_id="synthetic")
    result = {"ok": True, "dry_run_only": True, "execution_status": "dry_run"}
    monkeypatch.setattr(main, "_runtime_settings", lambda **kwargs: settings)
    monkeypatch.setattr(main, "_build_runtime_report", lambda *a, **kw: {})
    monkeypatch.setattr(main, "run_strategy_cycle", lambda **kw: dict(result))
    monkeypatch.setattr(main, "attach_strategy_result_execution_receipt", lambda *a, **kw: None)
    monkeypatch.setattr(main, "finalize_runtime_report", lambda *a, **kw: None)
    monkeypatch.setattr(main, "_strategy_result_summary", lambda *a, **kw: {})
    monkeypatch.setattr(main, "_strategy_result_diagnostics", lambda *a, **kw: {})
    monkeypatch.setattr(main, "strategy_result_report_status", lambda *a: "ok")
    monkeypatch.setattr(main, "_persist_runtime_report", lambda *a: None)
    monkeypatch.setattr(main, "build_sender", lambda *a: (_ for _ in ()).throw(AssertionError("preview sent")))
    output = main._run_strategy_cycle_with_report()
    assert output["notification_consolidation"]["route"] == "preview"
    assert output["notification_sent"] is False


@pytest.mark.parametrize("flag", [None, "false", "true"])
def test_actual_main_archives_before_staging_and_never_sends_routine(flag, monkeypatch):
    import main
    from application import rebalance_service
    from quant_platform_kit.common.runtime_reports import build_runtime_report_cloud_uri
    now, result, report, env = fixtures()
    result["strategy_run_stage"] = "PENDING_RECONCILIATION"
    settings = SimpleNamespace(dry_run_only=False, tg_token="synthetic-token", tg_chat_id="synthetic-chat", notify_lang="en")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("EXECUTION_REPORT_GCS_URI", "gs://synthetic/reports")
    monkeypatch.delenv("QSL_EXECUTION_REPORT_GCS_URI", raising=False)
    monkeypatch.delenv("FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID", raising=False)
    if flag is None:
        monkeypatch.delenv(unified.FLAG, raising=False)
    else:
        monkeypatch.setenv(unified.FLAG, flag)
    order, sent = [], []
    store = MemoryStore()
    monkeypatch.setattr(main, "_runtime_settings", lambda **kwargs: settings)
    monkeypatch.setattr(main, "_build_runtime_report", lambda *a, **kw: copy.deepcopy(report))
    def run(**kwargs):
        assert kwargs["send_cycle_notification"] is False
        return copy.deepcopy(result)
    monkeypatch.setattr(main, "run_strategy_cycle", run)
    monkeypatch.setattr(main, "attach_strategy_result_execution_receipt", lambda *a, **kw: None)
    def archive(value):
        uri = build_runtime_report_cloud_uri(value, cloud_prefix_uri="gs://synthetic/reports")
        value.setdefault("artifacts", {})["runtime_report_cloud_uri"] = uri
        order.append("archived")
        return uri
    monkeypatch.setattr(main, "_persist_runtime_report", archive)
    monkeypatch.setattr(unified, "GcsJsonStore", lambda: store)
    monkeypatch.setattr(rebalance_service, "_publish_cycle_notification", lambda *a, **kw: sent.append("routine"))
    original = unified.persist_report_candidate
    def persist(*args, **kwargs):
        assert order == ["archived"]
        return original(*args, **kwargs)
    monkeypatch.setattr(unified, "persist_report_candidate", persist)
    output = main._run_strategy_cycle_with_report()
    assert output["notification_consolidation"]["candidate_persisted"] is True
    assert sent == []
    payload = next(value for _, value in store.values.values() if "runs" in value)
    assert payload["runs"][0]["source_run_id"] == "synthetic-run"
    assert payload["runs"][0]["fill_count"] is None


@pytest.mark.parametrize("env,expected", [
    ({"QSL_NOTIFY_LANG": " zh-CN ", "NOTIFY_LANG": "en"}, "zh"),
    ({"QSL_NOTIFY_LANG": " EN-us ", "NOTIFY_LANG": "zh"}, "en"),
    ({"QSL_NOTIFY_LANG": "  ", "NOTIFY_LANG": " EN-us "}, "en"),
    ({"QSL_NOTIFY_LANG": "  ", "NOTIFY_LANG": " "}, "zh"),
])
def test_new_coverage_locale_precedence(env, expected):
    assert unified.coverage_locale(env) == expected


def test_heartbeat_coverage_uses_qsl_locale(monkeypatch):
    from scripts import execution_report_heartbeat as heartbeat
    monkeypatch.setenv("QSL_NOTIFY_LANG", " EN-us ")
    monkeypatch.setenv("NOTIFY_LANG", "zh-CN")
    sent = []
    def fail(**kwargs):
        kwargs["send"]()
        return {"candidate_persisted": False, "failed": True}
    monkeypatch.setattr(unified, "notify_heartbeat", fail)
    monkeypatch.setattr(heartbeat, "_send_telegram", lambda text: sent.append(text) or True)
    assert heartbeat._send_normal_heartbeat("example", "detail", quiet_eligible=True) == 1
    assert "Notification candidate storage could not be confirmed" in sent[0]


@pytest.mark.parametrize("flag", [None, "false", "true"])
def test_scheduled_exporter_uses_per_run_path_for_all_flags(flag, monkeypatch):
    from scripts import emit_unified_digest_candidates as emitter
    if flag is None:
        monkeypatch.delenv(unified.FLAG, raising=False)
    else:
        monkeypatch.setenv(unified.FLAG, flag)
    calls = []
    monkeypatch.setattr(emitter, "emit_candidates", lambda env: calls.append(True) or {"uploaded": True})
    assert emitter.main() == 0
    assert calls == [True]


def test_scheduled_exporter_failure_sends_only_localized_coverage(monkeypatch):
    from scripts import emit_unified_digest_candidates as emitter
    from scripts import execution_report_heartbeat as heartbeat
    monkeypatch.setenv("QSL_NOTIFY_LANG", " EN-us ")
    monkeypatch.setattr(emitter, "emit_candidates", lambda env: (_ for _ in ()).throw(ValueError("private URI")))
    sent = []
    monkeypatch.setattr(heartbeat, "_send_telegram", lambda text: sent.append(text) or True)
    assert emitter.main() == 1
    assert sent == [unified.alert_text("candidate_write_failure", "en")]
    assert "private URI" not in sent[0]


@pytest.mark.parametrize("first_ack", [True, False])
def test_exporter_suppresses_an_already_attempted_same_run_coverage_alert(first_ack, monkeypatch, tmp_path):
    from scripts import emit_unified_digest_candidates as emitter
    from scripts import execution_report_heartbeat as heartbeat
    monkeypatch.setenv("RUNTIME_HEARTBEAT_COVERAGE_ACK_PATH", str(tmp_path / "coverage"))
    sent = []
    def send(text):
        sent.append(text)
        return first_ack if len(sent) == 1 else True
    def failed_heartbeat(**kwargs):
        kwargs["send"]()
        return {"candidate_persisted": False, "failed": True}
    monkeypatch.setattr(heartbeat, "_send_telegram", send)
    monkeypatch.setattr(unified, "notify_heartbeat", failed_heartbeat)
    monkeypatch.setattr(emitter, "emit_candidates", lambda env: (_ for _ in ()).throw(ValueError("private")))
    assert heartbeat._send_normal_heartbeat("example", "detail", quiet_eligible=True) == 1
    assert emitter.main() == 1
    assert len(sent) == 1
    assert all("private" not in message for message in sent)


def test_exporter_retries_when_attempt_marker_cannot_be_written(monkeypatch, tmp_path):
    from scripts import emit_unified_digest_candidates as emitter
    from scripts import execution_report_heartbeat as heartbeat
    monkeypatch.setenv("RUNTIME_HEARTBEAT_COVERAGE_ACK_PATH", str(tmp_path / "missing" / "coverage"))
    sent = []
    monkeypatch.setattr(heartbeat, "_send_telegram", lambda text: sent.append(text) or False)
    monkeypatch.setattr(unified, "notify_heartbeat", lambda **kw: (kw["send"](), {"candidate_persisted": False, "failed": True})[1])
    monkeypatch.setattr(emitter, "emit_candidates", lambda env: (_ for _ in ()).throw(ValueError("private")))
    assert heartbeat._send_normal_heartbeat("example", "detail", quiet_eligible=True) == 1
    assert emitter.main() == 1
    assert len(sent) == 2


@pytest.mark.parametrize("flag", [None, "false", "true"])
def test_manual_healthy_heartbeat_stages_without_sending(flag, monkeypatch):
    from scripts import execution_report_heartbeat as heartbeat
    if flag is None:
        monkeypatch.delenv(unified.FLAG, raising=False)
    else:
        monkeypatch.setenv(unified.FLAG, flag)
    monkeypatch.setenv("RUNTIME_HEARTBEAT_NOTIFY_ON_SUCCESS", "true")
    staged, sent = [], []
    monkeypatch.setattr(unified, "notify_heartbeat", lambda **kw: staged.append(kw) or {"candidate_persisted": True})
    monkeypatch.setattr(heartbeat, "_send_telegram", lambda message: sent.append(message))
    assert heartbeat._send_normal_heartbeat("example", "detail", quiet_eligible=True) == 0
    assert sent == [] and staged[0]["kind"] == "healthy_heartbeat"


@pytest.mark.parametrize("flag", [None, "false", "true"])
def test_healthy_heartbeat_exports_daily_candidate_all_flags(flag, monkeypatch):
    now, _, _, env = fixtures()
    if flag is not None:
        env[unified.FLAG] = flag
    exports, sent = [], []
    monkeypatch.setattr(unified, "emit_candidates", lambda *a, **kw: exports.append(True) or {"uploaded": True})
    outcome = unified.notify_heartbeat(kind="healthy_heartbeat", env=env, now=now, store=MemoryStore(),
        send=lambda: sent.append("routine"))
    assert outcome["candidate_persisted"] is True
    assert exports == [True] and sent == []


@pytest.mark.parametrize("locale", ["zh", "en"])
def test_healthy_heartbeat_storage_failure_uses_only_coverage_error(locale, monkeypatch):
    now, _, _, env = fixtures()
    sent = []
    class BrokenStore:
        def read(self, key):
            raise RuntimeError("private cloud path")
    monkeypatch.setattr(unified, "emit_candidates", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("private data")))
    outcome = unified.notify_heartbeat(kind="healthy_heartbeat", env=env, now=now, store=BrokenStore(),
        send=lambda: sent.append(unified.alert_text("candidate_write_failure", locale)))
    assert outcome["failed"] is True
    assert sent == [unified.alert_text("candidate_write_failure", locale)]
    assert "private" not in sent[0]
