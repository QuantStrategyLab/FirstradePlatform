"""Actual adapter reservations must not turn local identities into delivery."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace

import pytest

from notifications import unified
from quant_platform_kit.notifications.unified_contract import order_identity_digest
from notifications.telegram import build_sender
from test_notification_consolidation import ENV, NOW, TARGET, Store, source

CASES = ["boolean", "boolean_false", "missing", "null", "empty", "blank", "negative_ack", "transport_ack",
         "transport_ack_wrapped", "transport_ack_path", "transport_ack_underscore",
         "transport_ack_case", "incident", "transition", "event", "order_digest",
         "incident_uri", "transition_wrapped", "event_wrapped", "transition_upper",
         "provider", "provider_numeric", "provider_hash"]


def outcome_for(case, event, marker_key, marker):
    identities = {"incident": marker_key, "transition": marker["transition_key"],
                  "event": event.to_candidate()["dedupe_key"], "order_digest": event.order_execution_digest}
    if case == "boolean":
        return True
    if case == "boolean_false":
        return False
    if case == "missing":
        return {"acknowledged": True}
    receipt = {
        "null": None, "empty": "", "blank": "  ", "negative_ack": "provider-message-unacknowledged",
        "transport_ack": "transport-ack:" + hashlib.sha256(b"arbitrary-local-transport-ack").hexdigest(),
        "transport_ack_wrapped": "sender:transport-ack:arbitrary-local-claim",
        "transport_ack_path": "transport-ack/arbitrary-local-claim",
        "transport_ack_underscore": "transport_ack:arbitrary-local-claim",
        "transport_ack_case": "  Transport-Ack:another-unrelated-value  ",
        "incident_uri": "gs://synthetic/notification-markers/" + marker_key + ".json",
        "transition_wrapped": "notification-transition:" + marker["transition_key"],
        "event_wrapped": "qsl.notification_event.v1:" + identities["event"],
        "transition_upper": marker["transition_key"].upper(),
        "provider": "independent-send-history/message-unique-21", "provider_numeric": "4711",
        "provider_hash": hashlib.sha256(b"independent-provider-returned-receipt").hexdigest(),
        **identities,
    }[case]
    return {"acknowledged": case not in {"negative_ack", "boolean_false"}, "receipt_id": receipt}


@pytest.mark.parametrize("case", CASES)
def test_actual_adapter_receipt_identity_and_double_call(case):
    store, calls, reserved = Store(), [], []
    report, _ = source()
    event = unified._event(report, ENV, "real_error", reason="synthetic-error",
                           order_digest=order_identity_digest("synthetic-order"))
    def send():
        calls.append("send")
        uri, (_, marker) = next((key, pair) for key, pair in store.data.items() if "/_notification_markers/" in key)
        assert marker["delivery_status"] == "pending" and marker["receipt_id"] is None
        reserved.append(deepcopy(marker))
        return outcome_for(case, event, uri.rsplit("/", 1)[-1].removesuffix(".json"), marker)
    first = unified._tracked(event, ENV, now=NOW, store=store, send=send)
    marker = next(value for key, (_, value) in store.data.items() if "/_notification_markers/" in key)
    delivered = case.startswith("provider")
    assert first["delivery_status"] == marker["delivery_status"] == ("delivered" if delivered else "uncertain")
    assert first["transport_acknowledged"] is (case not in {"negative_ack", "boolean_false"})
    assert bool(marker["receipt_id"]) is delivered
    for name in ("active", "generation", "state", "transition_key"):
        assert marker[name] == reserved[0][name]
    second = unified._tracked(event, ENV, now=NOW, store=store, send=send)
    assert second["delivery_status"] == "duplicate" and calls == ["send"]
    assert next(value for key, (_, value) in store.data.items() if "/_notification_markers/" in key) == marker


def test_real_sender_boolean_does_not_promote_provider_response_to_receipt():
    store, calls = Store(), []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: {"ok": True, "result": {"message_id": 4711}})
    sender = build_sender("synthetic-token", "synthetic-chat", requests_module=SimpleNamespace(post=post))
    report, _ = source()
    event = unified._event(report, ENV, "real_error", reason="synthetic-error")
    def send():
        return sender("synthetic alert")
    for _ in range(2):
        unified._tracked(event, ENV, now=NOW, store=store, send=send)
    marker = next(value for key, (_, value) in store.data.items() if "/_notification_markers/" in key)
    assert len(calls) == 1 and marker["delivery_status"] == "uncertain" and marker["receipt_id"] is None
    assert calls[0][1]["json"]["text"] == "synthetic alert" and calls[0][1]["timeout"] == 15


@pytest.mark.parametrize("case", ["boolean", "missing", "blank", "transport_ack", "incident",
    "transition", "event", "incident_uri", "transition_wrapped", "event_wrapped",
    "provider", "provider_numeric", "provider_hash"])
def test_combined_heartbeat_rejects_any_reservations_local_identity(monkeypatch, case):
    import json
    other = {**ENV, "RUNTIME_TARGET_JSON": json.dumps({**TARGET, "service_name": "other-service"}),
             "DIGEST_TARGET_ID": "other-target", "DIGEST_OPAQUE_ACCOUNT_UID": "other-opaque",
             "DIGEST_STORE_TARGET": "other"}
    env = {unified.FLAG: "true", "DIGEST_TARGET_BINDINGS_JSON": json.dumps([ENV, other]),
           "GITHUB_RUN_ID": "synthetic-monitor"}
    monkeypatch.setattr(unified, "emit_candidates", lambda *args, **kwargs: {"uploaded": True})
    store, calls = Store(), []
    observed = unified.NotificationEvent("receipt_missing", NOW, platform_id=TARGET["platform_id"],
        opaque_account_uid=ENV["DIGEST_OPAQUE_ACCOUNT_UID"], target_id=ENV["DIGEST_TARGET_ID"],
        strategy_profile=TARGET["strategy_profile"], incident_reason="execution_receipt",
        source_run_id=env["GITHUB_RUN_ID"])
    def send():
        calls.append("send")
        markers = [(uri, value) for uri, (_, value) in store.data.items() if "/_notification_markers/" in uri]
        assert len(markers) == 2 and all(marker["delivery_status"] == "pending" for _, marker in markers)
        uri, marker = markers[0]
        return outcome_for(case, observed, uri.rsplit("/", 1)[-1].removesuffix(".json"), marker)
    first = unified.notify_heartbeat(kind="receipt_missing", send=send, now=NOW, env=env, store=store)
    expected = "delivered" if case.startswith("provider") else "uncertain"
    assert len(first["deliveries"]) == 2
    assert all(row["delivery_status"] == expected and row["transport_acknowledged"] is True
               for row in first["deliveries"])
    persisted = {uri: deepcopy(value) for uri, (_, value) in store.data.items() if "/_notification_markers/" in uri}
    assert len(persisted) == 2 and all(marker["delivery_status"] == expected for marker in persisted.values())
    second = unified.notify_heartbeat(kind="receipt_missing", send=send, now=NOW, env=env, store=store)
    assert second["deliveries"] == [] and calls == ["send"]
    assert {uri: value for uri, (_, value) in store.data.items() if "/_notification_markers/" in uri} == persisted
