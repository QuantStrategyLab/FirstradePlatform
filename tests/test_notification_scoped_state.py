"""Scoped notification state keeps the shared candidate writer narrowly bound."""
import re
import json

import pytest

from notifications import unified
from test_notification_consolidation import ENV, NOW, Store, notify, source


STATE = "gs://synthetic/notification-markers/firstrade/synthetic-account"
ISOLATED = "gs://synthetic-isolated/candidates"


def test_candidate_override_wins_over_private_binding_and_preserves_marker_default():
    old = "gs://synthetic-bound/candidates"
    env = {**ENV, "DIGEST_CANDIDATES_OVERRIDE_GCS_URI": ISOLATED,
           "DIGEST_TARGET_BINDINGS_JSON": json.dumps([{"DIGEST_CANDIDATES_GCS_URI": old}])}
    binding, = unified._bindings(env)
    assert binding["DIGEST_CANDIDATES_GCS_URI"] == ISOLATED
    assert unified._state_prefix(binding) == old
    assert unified._bindings(binding) == [binding]
    assert json.loads(env["DIGEST_TARGET_BINDINGS_JSON"])[0]["DIGEST_CANDIDATES_GCS_URI"] == old


def test_candidate_override_preserves_existing_incident_deduplication():
    store, sent = Store(), []
    report, result = source(status="blocked", execution_blocked=True)
    env = {**ENV, "QSL_NOTIFICATION_MARKER_GCS_URI": STATE}
    notify(store, report, result, sent, env=env)
    migrated = {**env, "DIGEST_CANDIDATES_OVERRIDE_GCS_URI": ISOLATED}
    repeated = notify(store, report, result, sent, env=migrated)
    assert repeated["delivery_status"] == "duplicate"
    assert len(sent) == 1
    assert any(key.startswith(STATE + "/_notification_markers/") for key in store.data)
    binding, = unified._bindings(migrated)
    assert binding["DIGEST_CANDIDATES_GCS_URI"] == ISOLATED
    assert unified._state_prefix(binding) == STATE


@pytest.mark.parametrize("uri", ["https://synthetic/path", "gs://synthetic", "gs://synthetic/a/../b",
                                 "gs://synthetic/a?private=value", "gs://synthetic/a/*"])
def test_invalid_candidate_override_fails_before_storage(uri):
    with pytest.raises(ValueError, match="^candidate_store_configuration_missing$"):
        unified._bindings({**ENV, "DIGEST_CANDIDATES_OVERRIDE_GCS_URI": uri})


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
