"""Scoped notification state keeps the shared candidate writer narrowly bound."""
import re

from notifications import unified
from test_notification_consolidation import ENV, NOW, Store, notify, source


STATE = "gs://synthetic/notification-markers/firstrade/synthetic-account"


class ScopedStore(Store):
    def _check(self, key):
        assert key.startswith(STATE + "/") or re.fullmatch(
            r"gs://synthetic/candidates/firstrade/\d{4}-\d{2}-\d{2}/synthetic.json", key,
        ), "notification operation escaped the dedicated state or exact candidate"

    def read(self, key):
        self._check(key)
        return super().read(key)

    def compare_and_set(self, key, version, value):
        self._check(key)
        return super().compare_and_set(key, version, value)


def test_cycle_uses_dedicated_state_and_does_not_repeat_unknown_delivery():
    env = {**ENV, "QSL_NOTIFICATION_MARKER_GCS_URI": STATE + "/"}
    store, sent = ScopedStore(), []
    report, result = source(status="blocked", execution_blocked=True)
    first = notify(store, report, result, sent, env=env)
    second = notify(store, report, result, sent, env=env)
    assert first["delivery_status"] == "uncertain"
    assert second["delivery_status"] == "duplicate"
    assert len(sent) == 1
    assert any(key.startswith(STATE + "/_notification_markers/") for key in store.data)
    assert any("/candidates/firstrade/" in key for key in store.data)


def test_heartbeat_event_and_reservation_share_dedicated_state(monkeypatch):
    env = {**ENV, "QSL_NOTIFICATION_MARKER_GCS_URI": STATE}
    monkeypatch.setattr(unified, "emit_candidates", lambda *a, **kw: {"uploaded": True})
    store, sent = ScopedStore(), []
    for _ in range(2):
        unified.notify_heartbeat(kind="receipt_missing", send=lambda: sent.append(True) or True,
                                 now=NOW, env=env, store=store)
    assert len(sent) == 1
    assert any(key.startswith(STATE + "/_notification_events/") for key in store.data)
    assert any(key.startswith(STATE + "/_notification_markers/") for key in store.data)
