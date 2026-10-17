import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace

import pytest

from notifications import unified
from test_notification_consolidation import ENV, NOW, Store, TARGET, candidate, notify, source


def test_receipt_acceptance_quiet_and_due_rules_are_byte_identical_to_main():
    expected = json.loads(Path("tests/fixtures/notification_acceptance_main_c320fa0.json").read_text())
    text = Path("scripts/execution_report_heartbeat.py").read_text()
    tree = ast.parse(text)
    actual = {node.name: hashlib.sha256(ast.get_source_segment(text, node).encode()).hexdigest()
              for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in expected}
    assert actual == expected


def test_qpk_snapshot_matches_recorded_source_hashes():
    root = Path("notifications/digest_snapshot")
    provenance = json.loads((root / "provenance.json").read_text())
    assert provenance["source_sha"] == "07e24c0c804a9b47316b6ec3c75bb63820d8f421"
    for name, metadata in provenance["sources"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == metadata["sha256"]


def test_real_gcs_adapter_uses_generations_and_never_retries_unknown_writes():
    from google.api_core.exceptions import NotFound, PreconditionFailed
    class Blob:
        generation = None
        data = None
        fail_download = False
        unknown_write = False
        writes = 0
        def reload(self, **kwargs):
            assert kwargs == {"retry": None, "timeout": 30}
            if self.data is None:
                raise NotFound("missing")
        def download_as_text(self, **kwargs):
            assert kwargs == {"if_generation_match": self.generation, "retry": None, "timeout": 30}
            if self.fail_download:
                raise NotFound("history disappeared after metadata read")
            return self.data
        def upload_from_string(self, body, **kwargs):
            self.writes += 1
            assert kwargs["retry"] is None
            if self.unknown_write:
                raise TimeoutError("unknown write result")
            if kwargs["if_generation_match"] != (self.generation or 0):
                raise PreconditionFailed("conflict")
            self.data = body
            self.generation = (self.generation or 0) + 1
    blob = Blob()
    client = SimpleNamespace(bucket=lambda name: SimpleNamespace(blob=lambda key: blob))
    store = unified.GcsJsonStore(client)
    uri = "gs://synthetic/marker"
    assert store.read(uri) == (None, None)
    assert store.compare_and_set(uri, None, {"value": 1})
    assert store.read(uri) == (1, {"value": 1})
    assert not store.compare_and_set(uri, None, {"value": 2})
    blob.fail_download = True
    with pytest.raises(NotFound):
        store.read(uri)
    blob.unknown_write = True
    before = blob.writes
    with pytest.raises(TimeoutError):
        store.compare_and_set(uri, 1, {"value": 3})
    assert blob.writes == before + 1


def test_two_workers_reserve_one_incident_before_any_send():
    class ConcurrentStore(Store):
        def __init__(self):
            super().__init__()
            self.lock = Lock()
            self.barrier = Barrier(2)
            self.initial_reads = 0
        def read(self, key):
            with self.lock:
                result = super().read(key)
                race = "/_notification_markers/" in key and self.initial_reads < 2
                if race:
                    self.initial_reads += 1
            if race:
                self.barrier.wait(timeout=5)
            return result
        def compare_and_set(self, *args):
            with self.lock:
                return super().compare_and_set(*args)
    store, sent = ConcurrentStore(), []
    report, result = source(status="blocked", execution_blocked=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: notify(store, report, result, sent), range(2)))
    assert len(sent) == 1
    assert sorted(o["delivery_status"] for o in outcomes) == ["duplicate", "uncertain"]


def test_new_order_identity_is_not_hidden_by_unrecovered_old_order():
    store, sent = Store(), []
    first, pending = source(status="pending", orders=[{"status": "submitted", "broker_order_id": "order-a"}],
                             orders_pending_count=1)
    notify(store, first, pending, sent)
    second, other_pending = source("other", status="pending",
        orders=[{"status": "submitted", "broker_order_id": "order-b"}], orders_pending_count=1)
    notify(store, second, other_pending, sent, now=NOW + timedelta(minutes=1))
    assert len(sent) == 2


def test_candidate_slot_cannot_mix_accounts_even_if_misconfigured():
    store, sent = Store(), []
    report, result = source()
    notify(store, report, result, sent)
    other = {**ENV, "DIGEST_OPAQUE_ACCOUNT_UID": "another-opaque", "DIGEST_TARGET_ID": "another-target"}
    assert notify(store, report, result, sent, env=other)["route"] == "immediate_fallback"
    assert candidate(store)["runs"][0]["opaque_account_uid"] == "opaque-synthetic"


def test_main_on_writes_report_then_candidate_and_preserves_cycle_options(monkeypatch):
    import main
    import application.rebalance_service as rebalance
    store = Store()
    report, result = source()
    settings = SimpleNamespace(dry_run_only=False)
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(main, "_runtime_settings", lambda **kw: settings)
    monkeypatch.setattr(main, "_build_runtime_report", lambda *a, **kw: deepcopy(report))
    monkeypatch.setattr(main, "attach_strategy_result_execution_receipt", lambda *a, **kw: None)
    monkeypatch.setattr(main, "finalize_runtime_report", lambda *a, **kw: None)
    monkeypatch.setattr(main, "_persist_runtime_report", lambda *a: store.calls.append(("report",)) or "synthetic")
    monkeypatch.setattr(unified, "GcsJsonStore", lambda: store)
    def cycle(**kwargs):
        store.calls.append(("cycle", kwargs["send_cycle_notification"], kwargs["dispatch_plugin_alerts"]))
        return deepcopy(result)
    monkeypatch.setattr(main, "run_strategy_cycle", cycle)
    monkeypatch.setattr(rebalance, "_publish_cycle_notification", lambda *a, **kw: pytest.fail("routine direct send"))
    returned = main._run_strategy_cycle_with_report()
    assert store.calls[0] == ("cycle", False, True)
    assert store.calls[1] == ("report",)
    assert returned["notification_consolidation"]["candidate_persisted"] is True
    assert returned["notification_sent"] is False


def test_alert_text_is_single_language_and_does_not_echo_unknown_tokens():
    for kind in ("real_error", "receipt_missing", "receipt_unacceptable", "PRIVATE_SENTINEL"):
        zh, en = unified.alert_text(kind, "zh"), unified.alert_text(kind, "en")
        assert "PRIVATE_SENTINEL" not in zh + en
        assert not any("a" <= c.lower() <= "z" for c in zh)
        assert not any("\u4e00" <= c <= "\u9fff" for c in en)


def test_heartbeat_only_claims_affected_enabled_target(monkeypatch):
    first = dict(ENV)
    other_target = {**TARGET, "service_name": "other-service", "account_scope": "other-scope"}
    other = {**ENV, "RUNTIME_TARGET_JSON": json.dumps(other_target), "DIGEST_TARGET_ID": "other-target",
             "DIGEST_OPAQUE_ACCOUNT_UID": "other-opaque", "DIGEST_STORE_TARGET": "other"}
    disabled = {**ENV, "RUNTIME_TARGET_ENABLED": "false"}
    env = {unified.FLAG: "true", "DIGEST_TARGET_BINDINGS_JSON": json.dumps([first, other, disabled])}
    monkeypatch.setattr(unified, "emit_candidates", lambda *a, **kw: {"uploaded": True})
    store, sent = Store(), []
    outcome = unified.notify_heartbeat(kind="receipt_missing", send=lambda: sent.append(True) or True,
        now=NOW, env=env, store=store, affected_keys=["other-service|synthetic|other-scope"])
    assert len(sent) == 1 and len(outcome["deliveries"]) == 1
    markers = [value for key, (_, value) in store.data.items() if "/_notification_markers/" in key]
    assert len(markers) == 1


def test_healthy_candidate_write_failure_is_an_alert_and_is_not_repeated(monkeypatch):
    monkeypatch.setattr(unified, "emit_candidates", lambda *a, **kw: {"uploaded": False})
    store, sent = Store(), []
    for offset in (0, 1):
        result = unified.notify_heartbeat(kind="healthy_heartbeat", send=lambda: sent.append("storage failure") or True,
            now=NOW + timedelta(minutes=offset), env=ENV, store=store)
        assert result["candidate_persisted"] is False
    assert sent == ["storage failure"]
