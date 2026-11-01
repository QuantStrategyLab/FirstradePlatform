"""Offline failure injection at the candidate persistence boundary."""
import json
from types import SimpleNamespace

import pytest
from google.api_core.exceptions import Forbidden, NotFound, PreconditionFailed

from notifications import unified
from scripts import emit_unified_digest_candidates as entry
from test_notification_consolidation import ENV, NOW, Store


def idle_env():
    return {**ENV, "DIGEST_NOTIFICATION_SCHEDULE_JSON": json.dumps({
        "scheduler": {"main_time": "0 0 1 1 *", "timezone": "UTC"}})}


def test_first_candidate_after_not_found_uses_create_precondition():
    writes = []

    class Blob:
        def reload(self, **kwargs):
            raise NotFound("synthetic-private-location")

        def upload_from_string(self, body, **kwargs):
            writes.append((json.loads(body), kwargs))

    client = SimpleNamespace(bucket=lambda _: SimpleNamespace(blob=lambda _: Blob()))
    result = unified.emit_candidates(idle_env(), now=NOW, read=lambda _: [],
                                     store=unified.GcsJsonStore(client))
    assert result["uploaded"] is True
    assert len(writes) == 1
    body, options = writes[0]
    assert options["if_generation_match"] == 0 and options["retry"] is None
    assert body["runs"][0]["actually_ran"] is False
    assert "order_count" not in body["runs"][0]


@pytest.mark.parametrize("operation,error,stage,reason", [
    ("read", Forbidden("synthetic-private-location"), "candidate_read", "permission_denied"),
    ("cas", Forbidden("synthetic-private-location"), "candidate_write", "permission_denied"),
    ("cas", TimeoutError("synthetic-private-token"), "candidate_write", "timeout"),
    ("read", ValueError("synthetic-private-body"), "candidate_read", "invalid_data"),
])
def test_entry_reports_redacted_stage_and_never_retries_unknown_io(
    monkeypatch, capsys, operation, error, stage, reason,
):
    from scripts import execution_report_heartbeat as heartbeat
    calls, alerts = [], []

    class FailedStore(Store):
        def read(self, uri):
            calls.append("read")
            if operation == "read":
                raise error
            return None, None

        def compare_and_set(self, *args):
            calls.append("cas")
            raise error

    monkeypatch.delenv("DIGEST_TARGET_BINDINGS_JSON", raising=False)
    monkeypatch.delenv("RUNTIME_HEARTBEAT_COVERAGE_ACK_PATH", raising=False)
    monkeypatch.setenv("QSL_NOTIFY_LANG", "zh")
    monkeypatch.setattr(entry, "emit_candidates", lambda _: unified.emit_candidates(
        idle_env(), now=NOW, read=lambda _: [], store=FailedStore()))
    monkeypatch.setattr(heartbeat, "_send_telegram", lambda text: alerts.append(text))
    assert entry.main() == 1
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["stage"] == stage and result["reason"] == reason
    assert result["uploaded"] is False
    assert calls == (["read"] if operation == "read" else ["read", "cas"])
    assert len(alerts) == 1
    assert "阶段" in alerts[0] and "原因" in alerts[0]
    assert "synthetic-private" not in output + alerts[0]


def test_storage_serialization_failure_is_distinct_and_does_not_upload():
    from notifications.candidate_diagnostics import CandidateOperationError
    client = SimpleNamespace(bucket=lambda _: pytest.fail("serialization must precede I/O"))
    with pytest.raises(CandidateOperationError) as caught:
        unified.GcsJsonStore(client).compare_and_set("gs://synthetic/candidate", None,
                                                   {"value": float("nan")})
    assert caught.value.stage == "storage_serialize"
    assert caught.value.reason == "invalid_data"


def test_cas_conflicts_remain_bounded_and_distinct():
    from notifications.candidate_diagnostics import CandidateOperationError
    calls = []

    class Blob:
        def reload(self, **kwargs):
            raise NotFound("synthetic-private-location")

        def upload_from_string(self, *args, **kwargs):
            calls.append(1)
            raise PreconditionFailed("synthetic-private-location")

    client = SimpleNamespace(bucket=lambda _: SimpleNamespace(blob=lambda _: Blob()))
    with pytest.raises(CandidateOperationError) as caught:
        unified.emit_candidates(idle_env(), now=NOW, read=lambda _: [],
                                store=unified.GcsJsonStore(client))
    assert caught.value.stage == "candidate_merge"
    assert caught.value.reason == "conflict"
    assert len(calls) == 8


def test_existing_alert_ack_still_suppresses_duplicate(monkeypatch, tmp_path, capsys):
    from notifications.candidate_diagnostics import CandidateOperationError
    from scripts import execution_report_heartbeat as heartbeat
    marker = tmp_path / "ack"
    marker.write_text("acknowledged")
    monkeypatch.setenv("RUNTIME_HEARTBEAT_COVERAGE_ACK_PATH", str(marker))
    monkeypatch.delenv("DIGEST_TARGET_BINDINGS_JSON", raising=False)
    monkeypatch.setattr(entry, "emit_candidates", lambda _: (_ for _ in ()).throw(
        CandidateOperationError("candidate_write", "unknown")))
    monkeypatch.setattr(heartbeat, "_send_telegram", lambda _: pytest.fail("duplicate alert"))
    assert entry.main() == 1
    assert json.loads(capsys.readouterr().out)["stage"] == "candidate_write"


@pytest.mark.parametrize("locale", ["zh", "en"])
def test_heartbeat_first_alert_has_stage_and_retains_ack_dedup(monkeypatch, tmp_path, capsys, locale):
    from notifications.candidate_diagnostics import CandidateOperationError
    from scripts import execution_report_heartbeat as heartbeat
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("DIGEST_TARGET_BINDINGS_JSON", raising=False)
    monkeypatch.setenv("QSL_NOTIFY_LANG", locale)
    marker = tmp_path / "coverage-ack"
    monkeypatch.setenv("RUNTIME_HEARTBEAT_COVERAGE_ACK_PATH", str(marker))
    monkeypatch.setattr(unified, "GcsJsonStore", lambda: Store())
    def fail(*args, **kwargs):
        raise CandidateOperationError("candidate_write", "permission_denied")
    monkeypatch.setattr(unified, "emit_candidates", fail)
    alerts = []
    monkeypatch.setattr(heartbeat, "_send_telegram", lambda text: alerts.append(text) or True)
    assert heartbeat._send_normal_heartbeat("synthetic", "private-detail", now=NOW) == 1
    assert marker.read_text().strip() == "acknowledged"
    monkeypatch.setattr(entry, "emit_candidates", fail)
    assert entry.main() == 1
    assert len(alerts) == 1
    assert ("候选写入" if locale == "zh" else "candidate write") in alerts[0]
    assert ("权限被拒绝" if locale == "zh" else "permission denied") in alerts[0]
    assert "private-detail" not in alerts[0] + capsys.readouterr().out


def test_archive_read_failure_never_reaches_candidate_store():
    from notifications.candidate_diagnostics import CandidateOperationError
    store = Store()
    def fail(_):
        raise ValueError("synthetic-private-archive-output")
    with pytest.raises(CandidateOperationError) as caught:
        unified.emit_candidates(idle_env(), now=NOW, read=fail, store=store)
    assert caught.value.stage == "archive_list"
    assert caught.value.reason == "invalid_data"
    assert store.calls == []


def test_unknown_exceptions_and_untrusted_diagnostics_are_not_echoed():
    from notifications.candidate_diagnostics import CandidateOperationError, classify, failure_text
    error = RuntimeError("synthetic-private-body\nAuthorization: Bearer synthetic-secret")
    diagnostic = classify(error, "projection")
    assert diagnostic.reason == "unknown"
    forged = CandidateOperationError("private-stage", "private-reason")
    forged.stage, forged.reason = "private-stage", "private-reason"
    for locale in ("zh", "en"):
        text = failure_text(diagnostic, locale) + failure_text(forged, locale)
        assert "private" not in text and "synthetic-secret" not in text
