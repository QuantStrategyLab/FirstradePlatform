from __future__ import annotations

import pytest

from application.account_facts_publisher import (
    AccountFactsPublishConfig,
    AccountFactsPublishError,
    build_firstrade_account_snapshot,
    load_account_facts_publish_config,
    publish_firstrade_account_snapshot,
)


def config(**overrides):
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
    return values


def observation():
    return {
        "status": "available",
        "platform": "firstrade",
        "account_selector_status": "matched",
        "broker_account_id": "synthetic-native-id",
        "observed_started_at": "2026-10-08T08:00:00+00:00",
        "observed_finished_at": "2026-10-08T08:00:03+00:00",
        "balances": {
            "provider_equity": "1234.50",
            "cash_balance": "250.25",
            "available_cash": "240.00",
            "buying_power": "900.00",
            "currency": "USD",
        },
        "positions": [{"symbol": "SYNTH", "market_value": "999"}],
    }


def test_builds_fixed_snapshot_with_only_provider_equity_and_cash():
    cfg = load_account_facts_publish_config(config())

    payload = build_firstrade_account_snapshot(observation(), cfg, account_scope="us")

    assert payload == {
        "schema_version": "firstrade_account_snapshot_history.v1",
        "snapshot_schema_version": "firstrade_account_snapshot.v1",
        "account_scope": "us",
        "target_id": "firstrade-live-synthetic",
        "source_binding": {"kind": "deployment_runtime_account", "status": "bound", "id": "a" * 64},
        "observed_started_at": "2026-10-08T08:00:00+00:00",
        "observed_finished_at": "2026-10-08T08:00:03+00:00",
        "snapshot_atomic": False,
        "observation_date": "2026-10-08",
        "broker_account_id": "synthetic-native-id",
        "broker_reported_balances": [{"currency": "USD", "net_assets": "1234.50"}],
        "cash": [{
            "currency": "USD",
            "cash_balance": "250.25",
            "source_tag": "provider.cash_balance",
        }],
    }
    assert "positions" not in payload
    assert "available_cash" not in repr(payload)
    assert "buying_power" not in repr(payload)


def test_defaults_missing_provider_currency_to_usd():
    data = observation()
    data["balances"]["currency"] = None
    payload = build_firstrade_account_snapshot(
        data, load_account_facts_publish_config(config()), account_scope="us"
    )
    assert payload["broker_reported_balances"][0]["currency"] == "USD"


@pytest.mark.parametrize("value", ["", "usd", "US", "USDX", "USD "])
def test_rejects_invalid_provider_currency(value):
    data = observation()
    data["balances"]["currency"] = value

    with pytest.raises(AccountFactsPublishError, match="provider_currency_unavailable"):
        build_firstrade_account_snapshot(data, load_account_facts_publish_config(config()), account_scope="us")


def test_rejects_missing_provider_equity_and_account_mismatch():
    cfg = load_account_facts_publish_config(config())
    no_equity = observation()
    no_equity["balances"]["provider_equity"] = None
    with pytest.raises(AccountFactsPublishError, match="provider_equity_unavailable"):
        build_firstrade_account_snapshot(no_equity, cfg, account_scope="us")

    wrong_account = observation()
    wrong_account["broker_account_id"] = "different-synthetic-id"
    with pytest.raises(AccountFactsPublishError, match="account_identity_mismatch"):
        build_firstrade_account_snapshot(wrong_account, cfg, account_scope="us")


@pytest.mark.parametrize("value", ["1000000000000000", "0.123456789", "1e3", "1,000", "NaN"])
def test_rejects_qrs_amounts_outside_exact_decimal_contract(value):
    data = observation()
    data["balances"]["provider_equity"] = value

    with pytest.raises(AccountFactsPublishError):
        build_firstrade_account_snapshot(data, load_account_facts_publish_config(config()), account_scope="us")


def test_allows_cash_to_be_absent_without_synthesizing_it():
    data = observation()
    data["balances"]["cash_balance"] = None

    payload = build_firstrade_account_snapshot(
        data, load_account_facts_publish_config(config()), account_scope="us"
    )

    assert payload["cash"] == []


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED": "false"}, "disabled"),
        ({"FIRSTRADE_ACCOUNT_FACTS_SYNC_URL": "http://qsl-strategy-switch-console.pigbibi.workers.dev/api/account-facts/sync"}, "destination_not_approved"),
        ({"FIRSTRADE_ACCOUNT_FACTS_SYNC_URL": "https://other.example/api/account-facts/sync"}, "destination_not_approved"),
        ({"FIRSTRADE_ACCOUNT_FACTS_SYNC_URL": "https://qsl-strategy-switch-console.pigbibi.workers.dev/api/account-facts/sync?next=other"}, "destination_not_approved"),
        ({"FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID": "A" * 64}, "source_binding_invalid"),
        ({"FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN": "same", "ACCOUNT_FACTS_SYNC_TOKEN": "same"}, "sync_token_not_dedicated"),
    ],
)
def test_config_is_explicit_and_rejects_unapproved_or_aliased_values(overrides, reason):
    with pytest.raises(AccountFactsPublishError, match=reason):
        load_account_facts_publish_config(config(**overrides))


@pytest.mark.parametrize(
    "alias_name",
    [
        "BINANCE_ACCOUNT_FACTS_SYNC_TOKEN",
        "STRATEGY_SWITCH_SYNC_TOKEN",
        "RECONCILIATION_RECOVERY_SYNC_TOKEN",
        "RECONCILIATION_RECOVERY_CONTROLLER_TOKEN",
    ],
)
def test_sync_token_must_be_distinct_from_other_sync_and_control_tokens(alias_name):
    with pytest.raises(AccountFactsPublishError, match="sync_token_not_dedicated"):
        load_account_facts_publish_config(config(**{alias_name: "synthetic-facts-token"}))


def test_private_configuration_is_excluded_from_config_repr():
    cfg = load_account_facts_publish_config(config())
    rendered = repr(cfg)

    assert "synthetic-facts-token" not in rendered
    assert "synthetic-native-id" not in rendered
    assert "firstrade-live-synthetic" not in rendered
    assert "a" * 64 not in rendered


class FakeResponse:
    status_code = 200
    content = b"{}"

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, response=None, fail=False):
        self.response = response
        self.fail = fail
        self.trust_env = True
        self.calls = []
        self.closed = False

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.fail:
            raise RuntimeError("synthetic transport error")
        return self.response

    def close(self):
        self.closed = True


def valid_ack(payload, cfg):
    return {
        "ok": True,
        "stored": True,
        "platform": "firstrade",
        "account_key": cfg.account_key,
        "target_id": cfg.target_id,
        "observation_date": payload["observation_date"],
        "observed_finished_at": payload["observed_finished_at"],
        "return": {"available": False},
    }


def test_publisher_uses_private_token_single_no_redirect_post_and_closes():
    cfg = load_account_facts_publish_config(config())
    payload = build_firstrade_account_snapshot(observation(), cfg, account_scope="us")
    session = FakeSession(FakeResponse(valid_ack(payload, cfg)))

    publish_firstrade_account_snapshot(payload, cfg, session_factory=lambda: session)

    assert session.trust_env is False
    assert session.closed is True
    assert len(session.calls) == 1
    args, kwargs = session.calls[0]
    assert args == (cfg.sync_url,)
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == (5, 15)
    assert kwargs["headers"] == {"Authorization": "Bearer synthetic-facts-token"}
    assert "broker_account_id" in kwargs["json"]


@pytest.mark.parametrize("ack_mutation", ["ok", "stored", "platform", "account_key", "target_id", "date", "finished", "echo_id"])
def test_publisher_rejects_wrong_ack_and_closes(ack_mutation):
    cfg = load_account_facts_publish_config(config())
    payload = build_firstrade_account_snapshot(observation(), cfg, account_scope="us")
    ack = valid_ack(payload, cfg)
    if ack_mutation == "ok":
        ack["ok"] = False
    elif ack_mutation == "stored":
        ack["stored"] = False
    elif ack_mutation == "platform":
        ack["platform"] = "other"
    elif ack_mutation == "account_key":
        ack["account_key"] = "other"
    elif ack_mutation == "target_id":
        ack["target_id"] = "other"
    elif ack_mutation == "date":
        ack["observation_date"] = "2026-10-07"
    elif ack_mutation == "finished":
        ack["observed_finished_at"] = "2026-10-08T08:00:04+00:00"
    else:
        ack["broker_account_id"] = "synthetic-native-id"
    session = FakeSession(FakeResponse(ack))

    with pytest.raises(AccountFactsPublishError, match="sync_ack_invalid"):
        publish_firstrade_account_snapshot(payload, cfg, session_factory=lambda: session)
    assert session.closed is True


def test_publisher_failure_is_fixed_no_retry_and_closes():
    cfg = load_account_facts_publish_config(config())
    payload = build_firstrade_account_snapshot(observation(), cfg, account_scope="us")
    session = FakeSession(fail=True)

    with pytest.raises(AccountFactsPublishError, match="sync_unavailable"):
        publish_firstrade_account_snapshot(payload, cfg, session_factory=lambda: session)

    assert len(session.calls) == 1
    assert session.closed is True


def test_publisher_rejects_oversized_ack_without_retry():
    cfg = load_account_facts_publish_config(config())
    payload = build_firstrade_account_snapshot(observation(), cfg, account_scope="us")
    response = FakeResponse(valid_ack(payload, cfg))
    response.content = b"x" * 8193
    session = FakeSession(response)

    with pytest.raises(AccountFactsPublishError, match="sync_ack_invalid"):
        publish_firstrade_account_snapshot(payload, cfg, session_factory=lambda: session)

    assert len(session.calls) == 1
    assert session.closed is True
