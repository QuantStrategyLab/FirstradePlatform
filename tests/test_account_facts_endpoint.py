from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("flask")

import main
from application.account_facts_readonly import AccountFactsUnavailable


def _configure(monkeypatch, **overrides):
    values = {
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED": "true",
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_URL": "https://qsl-strategy-switch-console.pigbibi.workers.dev/api/account-facts/sync",
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN": "synthetic-facts-token",
        "FIRSTRADE_ACCOUNT_FACTS_TARGET_ID": "firstrade-live-synthetic",
        "FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID": "a" * 64,
        "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_KEY": "firstrade-main",
        "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE": "us",
        "FIRSTRADE_ACCOUNT": "synthetic-native-id",
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    for name in (
        "ACCOUNT_FACTS_SYNC_TOKEN",
        "IBKR_ACCOUNT_FACTS_SYNC_TOKEN",
        "SCHWAB_ACCOUNT_FACTS_SYNC_TOKEN",
        "BINANCE_ACCOUNT_FACTS_SYNC_TOKEN",
        "EXECUTION_EVIDENCE_SYNC_TOKEN",
        "STRATEGY_SWITCH_SYNC_TOKEN",
        "RECONCILIATION_RECOVERY_SYNC_TOKEN",
        "RECONCILIATION_RECOVERY_CONTROLLER_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(main, "_runtime_settings", lambda: SimpleNamespace(runtime_target=SimpleNamespace(
        platform_id="firstrade",
        account_selector=("synthetic-native-id",),
        account_scope="us",
    )))


class FakeClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_sync_endpoint_reads_once_closes_and_returns_only_safe_ack(monkeypatch):
    _configure(monkeypatch)
    client = FakeClient()
    client_builds = []
    payloads = []
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: (client_builds.append(1), client)[1])
    observation = {
        "status": "available",
        "platform": "firstrade",
        "account_selector_status": "matched",
        "broker_account_id": "synthetic-native-id",
        "observed_started_at": "2026-10-08T08:00:00+00:00",
        "observed_finished_at": "2026-10-08T08:00:03+00:00",
        "balances": {"currency": "USD", "provider_equity": "100.00", "cash_balance": "50.00"},
        "positions": [{"symbol": "MUST_NOT_BE_SENT"}],
    }
    collector_calls = []

    def collect_balances_only(*_args, **kwargs):
        collector_calls.append(kwargs)
        return observation

    monkeypatch.setattr(main, "collect_firstrade_account_facts", collect_balances_only)
    monkeypatch.setattr(
        main,
        "publish_firstrade_account_snapshot",
        lambda payload, cfg: payloads.append((payload, cfg)),
    )

    response = main.app.test_client().post(
        "/account-facts-sync",
        headers={"Authorization": "Bearer synthetic-facts-token"},
        data="{}",
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "stored": True}
    assert len(client_builds) == 1
    assert collector_calls == [{"expected_account": "synthetic-native-id", "include_positions": False}]
    assert client.closed is True
    assert len(payloads) == 1
    posted, cfg = payloads[0]
    assert posted["broker_account_id"] == cfg.account_id
    assert "positions" not in posted
    assert "MUST_NOT_BE_SENT" not in response.get_data(as_text=True)
    assert "synthetic-native-id" not in response.get_data(as_text=True)



def test_sync_endpoint_accepts_cloud_scheduler_oidc_bearer(monkeypatch):
    _configure(monkeypatch)
    client = FakeClient()
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: client)
    observation = {
        "status": "available",
        "platform": "firstrade",
        "account_selector_status": "matched",
        "broker_account_id": "synthetic-native-id",
        "observed_started_at": "2026-10-08T08:00:00+00:00",
        "observed_finished_at": "2026-10-08T08:00:03+00:00",
        "balances": {"currency": "USD", "provider_equity": "100.00", "cash_balance": "50.00"},
    }
    monkeypatch.setattr(main, "collect_firstrade_account_facts", lambda *_args, **_kwargs: observation)
    published = []
    monkeypatch.setattr(main, "publish_firstrade_account_snapshot", lambda payload, _config: published.append(payload))

    response = main.app.test_client().post(
        "/account-facts-sync",
        headers={
            "Authorization": "Bearer aaa.bbb.ccc",
            "User-Agent": "Google-Cloud-Scheduler",
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "stored": True}
    assert len(published) == 1
    assert client.closed is True


def test_sync_endpoint_reuses_the_unique_protected_runtime_selector(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.delenv("FIRSTRADE_ACCOUNT", raising=False)
    client = FakeClient()
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: client)
    observation = {
        "status": "available",
        "platform": "firstrade",
        "account_selector_status": "matched",
        "broker_account_id": "synthetic-native-id",
        "observed_started_at": "2026-10-08T08:00:00+00:00",
        "observed_finished_at": "2026-10-08T08:00:03+00:00",
        "balances": {"currency": "USD", "provider_equity": "100.00", "cash_balance": None},
    }
    monkeypatch.setattr(main, "collect_firstrade_account_facts", lambda *_args, **_kwargs: observation)
    published = []
    monkeypatch.setattr(main, "publish_firstrade_account_snapshot", lambda payload, _config: published.append(payload))

    response = main.app.test_client().post(
        "/account-facts-sync", headers={"Authorization": "Bearer synthetic-facts-token"}
    )

    assert response.status_code == 200
    assert len(published) == 1
    assert published[0]["broker_account_id"] == "synthetic-native-id"
    assert client.closed is True


@pytest.mark.parametrize(
    ("overrides", "headers", "expected_status", "expected_error"),
    [
        ({"FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED": "false"}, {}, 404, "disabled"),
        ({}, {"Authorization": "Bearer wrong-token"}, 401, "unauthorized"),
        ({"FIRSTRADE_ACCOUNT_FACTS_TARGET_ID": ""}, {"Authorization": "Bearer synthetic-facts-token"}, 503, "configuration_incomplete"),
        ({"FIRSTRADE_ACCOUNT_FACTS_SYNC_URL": "https://other.example/api/account-facts/sync"}, {"Authorization": "Bearer synthetic-facts-token"}, 503, "destination_not_approved"),
    ],
)
def test_off_auth_or_invalid_configuration_causes_zero_broker_io(
    monkeypatch, overrides, headers, expected_status, expected_error
):
    _configure(monkeypatch, **overrides)
    calls = []
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: calls.append("build"))

    response = main.app.test_client().post("/account-facts-sync", headers=headers)

    assert response.status_code == expected_status
    assert response.get_json() == {"ok": False, "error": expected_error}
    assert calls == []


@pytest.mark.parametrize(
    "runtime_target",
    [
        None,
        SimpleNamespace(platform_id="other", account_selector=("synthetic-native-id",), account_scope="us"),
        SimpleNamespace(platform_id="firstrade", account_selector=("synthetic-native-id",), account_scope="other"),
    ],
)
def test_runtime_target_mismatch_is_rejected_before_broker_io(monkeypatch, runtime_target):
    _configure(monkeypatch)
    monkeypatch.setattr(main, "_runtime_settings", lambda: SimpleNamespace(runtime_target=runtime_target))
    calls = []
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: calls.append("build"))

    response = main.app.test_client().post(
        "/account-facts-sync", headers={"Authorization": "Bearer synthetic-facts-token"}
    )

    assert response.status_code == 409
    assert response.get_json() == {"ok": False, "error": "runtime_target_mismatch"}
    assert calls == []


@pytest.mark.parametrize("selectors", [(), ("synthetic-native-id", "other-id"), ("",)])
def test_empty_or_multiple_runtime_selectors_are_rejected_before_broker_io(monkeypatch, selectors):
    _configure(monkeypatch)
    monkeypatch.setattr(main, "_runtime_settings", lambda: SimpleNamespace(runtime_target=SimpleNamespace(
        platform_id="firstrade", account_selector=selectors, account_scope="us"
    )))
    calls = []
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: calls.append("build"))

    response = main.app.test_client().post(
        "/account-facts-sync", headers={"Authorization": "Bearer synthetic-facts-token"}
    )

    assert response.status_code == 409
    assert response.get_json() == {"ok": False, "error": "runtime_target_account_selector_invalid"}
    assert calls == []


def test_explicit_account_environment_conflict_is_rejected_before_broker_io(monkeypatch):
    _configure(monkeypatch, FIRSTRADE_ACCOUNT="different-synthetic-id")
    calls = []
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: calls.append("build"))

    response = main.app.test_client().post(
        "/account-facts-sync", headers={"Authorization": "Bearer synthetic-facts-token"}
    )

    assert response.status_code == 409
    assert response.get_json() == {"ok": False, "error": "account_identity_mismatch"}
    assert calls == []


def test_collection_failure_is_safe_and_client_still_closes(monkeypatch):
    _configure(monkeypatch)
    client = FakeClient()
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: client)
    monkeypatch.setattr(
        main,
        "collect_firstrade_account_facts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AccountFactsUnavailable("balances_unavailable")),
    )
    published = []
    monkeypatch.setattr(main, "publish_firstrade_account_snapshot", lambda *_args: published.append(True))

    response = main.app.test_client().post(
        "/account-facts-sync", headers={"Authorization": "Bearer synthetic-facts-token"}
    )

    assert response.status_code == 503
    assert response.get_json() == {"ok": False, "error": "balances_unavailable"}
    assert client.closed is True
    assert published == []
