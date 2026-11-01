"""Cached-only observations must also reach the configured funds archive."""
from copy import deepcopy

import pytest

import main
from application.account_facts_publisher import AccountFactsPublishError
from tests.test_account_facts_endpoint import FakeClient, _configure


@pytest.fixture
def archive_route(monkeypatch):
    _configure(monkeypatch, FIRSTRADE_PERSIST_ACCOUNT_SNAPSHOT="true")
    client = FakeClient()
    observation = {
        "status": "available", "platform": "firstrade",
        "account_selector_status": "matched", "broker_account_id": "synthetic-native-id",
        "observed_started_at": "2026-10-08T23:59:59+00:00",
        "observed_finished_at": "2026-10-09T00:00:03+00:00",
        "balances": {"currency": "USD", "provider_equity": "100.123456789",
                     "cash_balance": None, "available_cash": "25.01"},
        "positions_status": "available", "positions": [],
        "coverage": {"positions_requested": True, "positions_count": 0},
    }
    writes, publishes = [], []

    class Store:
        def write_json(self, key, value):
            writes.append((key, deepcopy(value)))
            return True

    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: client)
    monkeypatch.setattr(main, "collect_firstrade_account_facts", lambda *a, **k: observation)
    monkeypatch.setattr(main, "publish_firstrade_account_snapshot", lambda *a: publishes.append(a))
    # Patch the existing state-store boundary; no cloud or broker traffic.
    import application.session_check_service as service
    monkeypatch.setattr(service, "build_gcs_state_store_from_env", lambda *a: Store())
    return observation, writes, publishes, client, service


def invoke():
    return main.app.test_client().post(
        "/account-facts-sync", headers={"Authorization": "Bearer synthetic-facts-token"})


def test_cached_sync_archives_original_observation_before_publish(archive_route):
    observation, writes, publishes, client, _ = archive_route
    response = invoke()
    assert response.status_code == 200
    assert len(writes) == 2
    key, saved = writes[1]
    assert key.endswith("/funds/history/2026/10/08/20261008T235959Z.json")
    assert saved["as_of"] == observation["observed_started_at"]
    assert saved["balance_metrics"]["total_equity"] == "100.123456789"
    assert saved["balance_metrics"]["cash_balance"] is None
    assert saved["positions"] == []
    assert saved["coverage"] == observation["coverage"]
    assert len(publishes) == 1
    assert client.closed
    assert response.json == {"ok": True, "stored": True}
    assert "synthetic-native-id" not in response.get_data(as_text=True)


@pytest.mark.parametrize("failure", ["missing", "write"])
def test_archive_failure_blocks_success_and_qrs_publish(archive_route, monkeypatch, failure, capsys):
    _, writes, publishes, client, service = archive_route
    class FailedStore:
        def write_json(self, *a):
            raise RuntimeError("private-provider-text")
    monkeypatch.setattr(service, "build_gcs_state_store_from_env",
                        lambda *a: None if failure == "missing" else FailedStore())
    response = invoke()
    assert response.status_code == 503
    assert response.json == {"ok": False, "error": "funds_archive_unavailable"}
    assert publishes == []
    assert client.closed
    assert "private-provider-text" not in capsys.readouterr().out
    assert "private-provider-text" not in response.get_data(as_text=True)


def test_qrs_failure_keeps_real_archive(archive_route, monkeypatch):
    _, writes, _, _, _ = archive_route
    def reject(*a):
        raise AccountFactsPublishError("sync_unavailable")
    monkeypatch.setattr(main, "publish_firstrade_account_snapshot", reject)
    assert invoke().status_code == 502
    assert len(writes) == 2


def test_invalid_identity_writes_nothing(archive_route):
    observation, writes, publishes, _, _ = archive_route
    observation["broker_account_id"] = "different-synthetic-id"
    assert invoke().status_code == 503
    assert writes == publishes == []


def test_disabled_archive_has_no_storage_access(archive_route, monkeypatch):
    _, writes, publishes, _, service = archive_route
    monkeypatch.setenv("FIRSTRADE_PERSIST_ACCOUNT_SNAPSHOT", "false")
    monkeypatch.setattr(service, "build_gcs_state_store_from_env",
                        lambda *a: pytest.fail("archive disabled"))
    assert invoke().status_code == 200
    assert writes == []
    assert len(publishes) == 1


def test_unrequested_positions_stay_absent(archive_route):
    observation, writes, _, _, _ = archive_route
    observation.update(positions_status="not_requested", positions=None)
    assert invoke().status_code == 200
    assert "positions" not in writes[1][1]
