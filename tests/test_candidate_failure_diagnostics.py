"""Publisher diagnostics use fixed vocabulary and synthetic failures only."""
import json

import pytest
from google.api_core.exceptions import Forbidden

from notifications import unified
from scripts import emit_unified_digest_candidates as publisher
from scripts import execution_report_heartbeat as heartbeat
from test_notification_consolidation import ENV, NOW, Store


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("DIGEST_TARGET_BINDINGS_JSON", raising=False)
    monkeypatch.delenv("RUNTIME_HEARTBEAT_COVERAGE_ACK_PATH", raising=False)
    monkeypatch.setattr(heartbeat, "_send_telegram", lambda text: True)


def result(capsys):
    output = capsys.readouterr()
    assert "synthetic-private" not in output.out + output.err
    assert "gs://" not in output.out + output.err
    return json.loads(output.out)


@pytest.mark.parametrize("raw,code", [
    ('{"synthetic-private":', "json_invalid"),
    ('{"synthetic-private": true}', "candidate_bindings_invalid"),
    ('[{"QSL_ROUTINE_NOTIFICATION_MODE":"immediate"}]', "candidate_binding_mode_conflict"),
])
def test_binding_failure_is_reported_before_any_projection(raw, code, monkeypatch, capsys):
    monkeypatch.setenv("QSL_ROUTINE_NOTIFICATION_MODE", "daily_digest")
    monkeypatch.setenv("DIGEST_TARGET_BINDINGS_JSON", raw)
    monkeypatch.setattr(publisher, "emit_candidates", lambda env: pytest.fail("unexpected projection"))
    assert publisher.main() == 1
    assert result(capsys) == {"status": "candidate_projection_failed", "uploaded": False,
                              "stage": "binding_validation", "error_code": code}


@pytest.mark.parametrize("code,stage", [
    ("candidate_store_identity_missing", "binding_validation"),
    ("candidate_archive_read_failed", "archive_read"),
    ("candidate_source_absent", "source_selection"),
    ("candidate_due_source_absent", "schedule_evaluation"),
    ("candidate_identity_conflict", "candidate_merge"),
    ("candidate_concurrent_update", "candidate_write"),
    ("candidate_source_invalid", "projection"),
])
def test_known_failure_codes_are_bounded(code, stage, monkeypatch, capsys):
    def fail(env):
        raise ValueError(code)
    monkeypatch.setattr(publisher, "emit_candidates", fail)
    assert publisher.main() == 1
    output = result(capsys)
    assert output["stage"] == stage and output["error_code"] == code


@pytest.mark.parametrize("error", [
    ValueError("candidate_source_absent gs://synthetic-private/object"),
    KeyError("synthetic-private-account"),
    RuntimeError({"synthetic-private": "credential"}),
])
def test_unknown_errors_do_not_echo_values_or_guess_a_cause(error, monkeypatch, capsys):
    def fail(env):
        raise error
    monkeypatch.setattr(publisher, "emit_candidates", fail)
    assert publisher.main() == 1
    output = result(capsys)
    assert output["stage"] == "projection" and output["error_code"] == "unexpected_error"


@pytest.mark.parametrize("stage,error,code", [
    ("candidate_read", Forbidden("gs://synthetic-private/object"), "permission_denied"),
    ("candidate_write", Forbidden("synthetic-private-account"), "permission_denied"),
    ("candidate_write", TimeoutError("synthetic-private"), "timeout_unknown"),
    ("candidate_write", ValueError("synthetic-private"), "serialization_invalid"),
])
def test_real_merge_failure_keeps_stage_and_never_retries_unknown_write(stage, error, code, monkeypatch, capsys):
    calls = []
    class FailedStore:
        def read(self, uri):
            calls.append("read")
            if stage == "candidate_read":
                raise error
            return None, None
        def compare_and_set(self, *args):
            calls.append("write")
            raise error
    def emit(env):
        return unified._merge(FailedStore(), "gs://synthetic-private/object", {}, {}, idle={"actually_ran": False})
    monkeypatch.setattr(publisher, "emit_candidates", emit)
    assert publisher.main() == 1
    output = result(capsys)
    assert output["stage"] == stage and output["error_code"] == code
    assert output["error_category"] == code  # Preserve the existing storage diagnostic.
    assert calls == (["read"] if stage == "candidate_read" else ["read", "write"])


def test_tampered_storage_diagnostics_cannot_escape_allowlist(monkeypatch, capsys):
    error = unified.CandidateStorageError("gs://synthetic-private/object", RuntimeError())
    error.category = "synthetic-private-account"
    monkeypatch.setattr(publisher, "emit_candidates", lambda env: (_ for _ in ()).throw(error))
    assert publisher.main() == 1
    output = result(capsys)
    assert output["stage"] == "projection" and output["error_code"] == "storage_unknown"


def test_successful_idle_projection_uses_real_producer_without_diagnostics(monkeypatch, capsys):
    env = {**ENV, "DIGEST_NOTIFICATION_SCHEDULE_JSON": json.dumps({
        "scheduler": {"main_time": "0 20 1 * *", "timezone": "UTC"}})}
    store = Store()
    monkeypatch.setattr(publisher, "emit_candidates", lambda _: unified.emit_candidates(
        env, now=NOW, read=lambda args: [], store=store))
    assert publisher.main() == 0
    assert result(capsys) == {"status": "projected", "uploaded": True, "targets": 1}
    assert len(store.data) == 1


@pytest.mark.parametrize("raises", [False, True])
def test_alert_fallback_does_not_leak_or_replace_original_diagnostic(raises, monkeypatch, capsys):
    def fail(env):
        raise ValueError("candidate_source_absent")
    def send(text):
        import sys
        print("gs://synthetic-private/object")
        print("synthetic-private-credential", file=sys.stderr)
        if raises:
            raise RuntimeError("synthetic-private-account")
        return False
    monkeypatch.setattr(publisher, "emit_candidates", fail)
    monkeypatch.setattr(heartbeat, "_send_telegram", send)
    assert publisher.main() == 1
    assert result(capsys)["error_code"] == "candidate_source_absent"


def test_missing_source_uses_real_projection_and_preserves_empty_store(monkeypatch, capsys):
    store = Store()
    monkeypatch.setattr(publisher, "emit_candidates", lambda _: unified.emit_candidates(
        ENV, now=NOW, read=lambda args: [], store=store))
    assert publisher.main() == 1
    output = result(capsys)
    assert output["stage"] == "source_selection"
    assert output["error_code"] == "candidate_source_absent"
    assert store.data == {}


def test_unserializable_output_has_its_own_stage(monkeypatch, capsys):
    monkeypatch.setattr(publisher, "emit_candidates", lambda _: {"synthetic-private": object()})
    assert publisher.main() == 1
    output = result(capsys)
    assert output["stage"] == "output_serialization"
    assert output["error_code"] == "serialization_invalid"
