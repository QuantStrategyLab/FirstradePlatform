from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Lock, Event

import pytest

from application import session_renewal_guard as guard
from application.firstrade_client import FirstradeMfaRequired
from application.session_check_service import SessionRenewalFailure

NOW = datetime(2026, 1, 2, 21, 0, tzinfo=timezone.utc)


class Store:
    def __init__(self):
        self.record, self.generation = None, 0
        self.alerts = {}
        self.lock = Lock()
        self.fail_read = False
        self.fail_write = None

    def read(self):
        with self.lock:
            if self.fail_read:
                raise RuntimeError("synthetic-private-data")
            return deepcopy(self.record), self.generation

    def write(self, value, generation):
        with self.lock:
            if value["status"] == self.fail_write:
                raise TimeoutError("synthetic-private-data")
            if generation != self.generation:
                return None
            self.record = deepcopy(value)
            self.generation += 1
            return self.generation

    def claim_alert(self, cycle, kind):
        with self.lock:
            key = (cycle, kind)
            if key in self.alerts:
                return "already_acknowledged" if self.alerts[key] == "acknowledged" else "unconfirmed"
            self.alerts[key] = "unknown"
            return True

    def finish_alert(self, cycle, kind, delivery):
        self.alerts[cycle, kind] = delivery


def run(store, renew, **kwargs):
    return guard.run_guarded_renewal(renew, store=store, now=NOW, clock=lambda: NOW, **kwargs)


def success():
    return {"ok": True, "session_renewed": True}


def test_duplicate_and_concurrent_delivery_only_log_in_once():
    store, entered, release = Store(), Event(), Event()
    calls, alerts = [], []

    def renew():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return success()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, store, renew, sender=lambda *x: alerts.append(x) or True)
        assert entered.wait(5)
        second = run(store, renew, sender=lambda *x: alerts.append(x) or True)
        assert second[0]["status"] == "not_replayed"
        release.set()
        assert first.result()[0]["status"] == "completed"
    assert run(store, renew)[0]["status"] == "duplicate_delivery"
    assert calls == [1] and alerts == []


def test_two_readers_of_absent_state_use_atomic_claim():
    from threading import Barrier
    store, barrier = Store(), Barrier(2)
    original_read = store.read

    def read():
        value = original_read()
        barrier.wait(5)
        return value

    store.read = read
    calls = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(store, lambda: calls.append(1) or success()), range(2)))
    assert calls == [1]
    assert {r[0]["status"] for r in results} == {"completed", "concurrent_delivery"}


@pytest.mark.parametrize("error", [FirstradeMfaRequired("private"),
    SessionRenewalFailure("auth_rejected", "auth"), TimeoutError("private")])
def test_failures_stop_and_block_future_cycles(error, capsys):
    store, calls, sent = Store(), [], []

    def renew():
        calls.append(1)
        raise error

    result, status = run(store, renew, sender=lambda *x: sent.append(x) or True)
    assert status in {409, 503}
    assert result["manual_recovery_required"] is True
    assert store.record["status"] == "manual_required"
    for offset in [0, 1, 3]:
        response, _ = guard.run_guarded_renewal(renew, store=store, now=NOW + timedelta(days=offset))
        assert response["status"] == "not_replayed"
    assert len(calls) == len(sent) == 1
    assert "private" not in capsys.readouterr().out


def test_unrecorded_failure_and_completion_timeout_keep_durable_running_claim():
    for fail_write in ["manual_required", "completed"]:
        store = Store()
        store.fail_write = fail_write
        def renew():
            if fail_write == "manual_required":
                raise TimeoutError()
            return success()
        assert run(store, renew, sender=lambda *_: True)[1] == 503
        assert store.record["status"] == "running"
        result, _ = guard.run_guarded_renewal(lambda: pytest.fail("replayed"), store=store,
                                             now=NOW + timedelta(days=1))
        assert result["status"] == "not_replayed"


@pytest.mark.parametrize("failure", ["read", "claim"])
def test_state_failure_prevents_credential_loading_or_login(failure):
    store, alerts = Store(), []
    store.fail_read = failure == "read"
    store.fail_write = "running" if failure == "claim" else None
    result, status = run(store, lambda: pytest.fail("login reached"), sender=lambda *x: alerts.append(x) or True)
    assert status == 503 and result["error"] == "guard_state_unavailable"
    assert len(alerts) == 1


def test_crash_does_not_release_claim():
    store = Store()
    def crash():
        raise SystemExit()
    with pytest.raises(SystemExit):
        run(store, crash)
    assert store.record["status"] == "running"
    assert run(store, lambda: pytest.fail("replayed"))[0]["status"] == "not_replayed"


def test_next_cycle_after_success_and_owner_recovery():
    for previous_status in ["completed", "ready"]:
        store, sent = Store(), []
        store.record = {"cycle": (NOW - timedelta(days=1)).isoformat(), "status": previous_status}
        result, status = run(store, success, sender=lambda *x: sent.append(x) or True)
        assert status == 200 and result["status"] == "completed"
        assert len(sent) == (previous_status == "ready")
        if sent:
            assert sent[0][0] == "recovered"


@pytest.mark.parametrize("header,status", [
    ("2026-01-01T21:00:00Z", 200), ("2026-01-03T21:00:00Z", 400),
    ("2026-01-02T20:00:00Z", 400), ("2026-01-02T21:00:00", 400), ("bad", 400),
])
def test_stale_future_and_invalid_schedule_never_login(header, status):
    result, actual = run(Store(), lambda: pytest.fail("login reached"), schedule_time=header)
    assert actual == status
    assert result["ok"] is False


def test_schedule_header_is_stable_across_midnight():
    store = Store()
    run(store, success)
    result, _ = guard.run_guarded_renewal(lambda: pytest.fail("login reached"), store=store,
        now=NOW + timedelta(hours=4), schedule_time=NOW.isoformat())
    assert result["status"] == "duplicate_delivery"


@pytest.mark.parametrize("record_kind", ["missing", "running", "failed", "yesterday", "late", "read_error", "ontime"])
def test_deadline_reads_only_exact_cycle_and_cutoff(record_kind):
    store, sent = Store(), []
    if record_kind != "missing":
        store.record = {"cycle": NOW.isoformat(), "status": "completed", "completed_at": (NOW + timedelta(minutes=10)).isoformat()}
    if record_kind in {"running", "failed"}:
        store.record["status"] = record_kind
    if record_kind == "yesterday":
        store.record["cycle"] = (NOW - timedelta(days=1)).isoformat()
    if record_kind == "late":
        store.record["completed_at"] = (NOW + timedelta(minutes=16)).isoformat()
    store.fail_read = record_kind == "read_error"
    args = dict(store=store, now=NOW + timedelta(minutes=20),
                schedule_time=(NOW + timedelta(minutes=15)).isoformat(), sender=lambda *x: sent.append(x) or True)
    result, status = guard.check_renewal_deadline(**args)
    assert status == 200
    assert result["ok"] is (record_kind == "ontime")
    guard.check_renewal_deadline(**args)
    assert len(sent) == (0 if record_kind == "ontime" else 1)


def test_deadline_not_due_and_stale_delivery():
    store = Store()
    store.fail_read = True
    for kwargs, expected in [
        ({"now": NOW}, "not_due"),
        ({"now": NOW + timedelta(days=1, minutes=15), "schedule_time": (NOW + timedelta(minutes=15)).isoformat()}, "stale_delivery"),
    ]:
        result, _ = guard.check_renewal_deadline(store=store, sender=lambda *_: pytest.fail("alert reached"), **kwargs)
        assert result["status"] == expected


def test_unconfirmed_notification_is_not_claimed_delivered_or_retried():
    store, sent = Store(), []
    args = dict(store=store, now=NOW + timedelta(minutes=15), sender=lambda *x: sent.append(x) or False)
    for _ in range(2):
        result, status = guard.check_renewal_deadline(**args)
        assert status == 503 and result["alert_delivery"] == "unconfirmed"
    assert len(sent) == 1


def test_alert_storage_failure_still_attempts_notification():
    store, sent = Store(), []
    def fail(*_):
        raise TimeoutError()
    store.claim_alert = fail
    result, _ = guard.check_renewal_deadline(store=store, now=NOW + timedelta(minutes=15),
                                           sender=lambda *x: sent.append(x) or True)
    assert result["alert_delivery"] == "acknowledged" and len(sent) == 1


def test_injected_notification_only(monkeypatch):
    from notifications import telegram
    calls = []
    monkeypatch.setenv("TELEGRAM_TOKEN", "synthetic-token")
    monkeypatch.setenv("GLOBAL_TELEGRAM_CHAT_ID", "synthetic-target")
    monkeypatch.delenv("QSL_GLOBAL_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(telegram, "build_sender", lambda token, chat: lambda message: calls.append((token, chat, message)) or True)
    assert guard.send_alert("deadline", NOW.isoformat(), "not_completed_by_2115_utc", "deadline")
    assert "21:15 UTC" in calls[0][2]
    monkeypatch.delenv("TELEGRAM_TOKEN")
    assert guard.send_alert("failure", NOW.isoformat(), "auth_rejected", "auth") is False


def test_generation_pinned_read_and_write(monkeypatch):
    from google.cloud import storage
    from google.api_core.exceptions import PreconditionFailed, NotFound
    from application.state_persistence import GcsStateStore
    calls = []
    class Blob:
        generation = 7
        missing = False
        conflict = False
        def reload(self, **kwargs):
            calls.append(("reload", kwargs))
            if self.missing:
                raise NotFound("missing")
        def download_as_text(self, **kwargs):
            calls.append(("read", kwargs))
            return '{"schema":1,"status":"running","cycle":"2026-01-02T21:00:00+00:00"}'
        def upload_from_string(self, *args, **kwargs):
            calls.append(("write", kwargs))
            if self.conflict:
                raise PreconditionFailed("conflict")
            self.generation = 8
    blob = Blob()
    class Client:
        def bucket(self, name):
            return self
        def blob(self, name):
            return blob
    monkeypatch.setattr(storage, "Client", Client)
    store = guard.RenewalStore(GcsStateStore("synthetic-bucket"))
    value, generation = store.read()
    assert generation == 7
    assert store.write(value, generation) == 8
    assert calls[1][1]["if_generation_match"] == 7
    assert calls[2][1]["if_generation_match"] == 7
    assert all(c[1]["retry"] is None for c in calls)
    blob.conflict = True
    assert store.write(value, generation) is None
    blob.missing = True
    assert store.read() == (None, 0)


@pytest.mark.parametrize("prefix", [None, "", "synthetic-prefix"])
def test_guard_uses_the_same_prefix_default_as_session_cache(monkeypatch, prefix):
    monkeypatch.setenv("FIRSTRADE_GCS_STATE_BUCKET", "synthetic-bucket")
    if prefix is None:
        monkeypatch.delenv("FIRSTRADE_STATE_PREFIX", raising=False)
    else:
        monkeypatch.setenv("FIRSTRADE_STATE_PREFIX", prefix)
    store = guard.RenewalStore.from_env()
    assert store.state.prefix == (prefix or "firstrade-platform")
