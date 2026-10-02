from __future__ import annotations

from datetime import datetime, timezone
import pytest

from application.firstrade_client import FirstradeCredentials, FirstradeMfaRequired
from application.session_check_service import (
    build_account_funds_snapshot,
    run_cached_balance_field_diagnostic,
    run_session_check,
    run_target_bound_account_session_refresh,
)


class FakeClient:
    def __init__(self, _credentials, *, live_trading_enabled=False):
        self.live_trading_enabled = live_trading_enabled
        self.session_reused = True

    def connect(self):
        return self

    def select_account(self, requested_account=None):
        return requested_account or "12345678"

    def list_account_summaries(self):
        return [{"account": "****5678", "total_value": "100.00"}]

    def get_balances(self, _account):
        return {
            "result": {
                "total_account_value": "100.00",
                "cash_balance": "40.00",
                "margin_buying_power": "80.00",
                "unrelated": "ignored",
            }
        }

    def get_positions(self, _account):
        return {
            "items": [
                {"symbol": "SPY", "quantity": "2", "market_value": "900.50"},
                {"ticker": "QQQ", "qty": "1", "value": "450.25"},
            ]
        }


class FakeStateStore:
    def __init__(self, reads=None):
        self.payloads = dict(reads or {})
        self.reads = []
        self.writes = []

    def read_json(self, key):
        self.reads.append(key)
        return self.payloads.get(key)

    def write_json(self, key, payload):
        self.writes.append((key, payload))
        self.payloads[key] = payload
        return True


class ExplodingClient:
    def __init__(self, *_args, **_kwargs):
        raise AssertionError("client should not be created when session-check is skipped")


class DiagnosticAccountData:
    account_numbers = ["synthetic-account-placeholder"]

    def __init__(self, payload):
        self.payload = payload
        self.balance_reads = []

    def get_account_balances(self, account):
        self.balance_reads.append(account)
        return self.payload


class DiagnosticClient:
    def __init__(self, _credentials, *, live_trading_enabled):
        assert live_trading_enabled is False
        self.session_reused = False
        self.account_data = DiagnosticAccountData(
            {
                "cash": {
                    "label": "Cash Balance",
                    "value": "DO_NOT_RETURN_AMOUNT",
                    "currency": "USD",
                    "A1": {
                        "currency": "DO_NOT_RETURN_DYNAMIC_UNIT",
                        "value": "DO_NOT_RETURN_DYNAMIC_AMOUNT",
                    },
                },
                "available": "DO_NOT_RETURN_AVAILABLE_VALUE",
                "AliceTrading": {"currency": "DO_NOT_RETURN_ALIAS_UNIT"},
                "12345678": {"private": "DO_NOT_RETURN_DYNAMIC_ACCOUNT_DATA"},
                "accountNumber": "DO_NOT_RETURN_ACCOUNT_VALUE",
            }
        )
        self.calls = []

    def has_fresh_cached_session(self):
        self.calls.append("cache_check")
        return True

    def connect_read_only(self):
        self.calls.append("connect_read_only")
        self.session_reused = True
        return self

    def connect(self):
        raise AssertionError("diagnostic must never use the login-capable connect path")

    def account_numbers(self):
        self.calls.append("account_numbers")
        return list(self.account_data.account_numbers)

    def select_account(self, account):
        self.calls.append("select_account")
        return account

    def require_connected(self):
        return object(), self.account_data

    def get_positions(self, _account):
        raise AssertionError("diagnostic must not read positions")

    def get_orders(self, _account, **_kwargs):
        raise AssertionError("diagnostic must not read orders")

    def close(self):
        self.calls.append("close")


def _env(values):
    return lambda name, default=None: values.get(name, default)


class AuthRefreshClient:
    def __init__(self, _credentials, *, live_trading_enabled=False, accounts=None, auth_error=None, cache_status="ok"):
        assert live_trading_enabled is False
        self.accounts = accounts if accounts is not None else ["synthetic-account-placeholder"]
        self.auth_error = auth_error
        self.cache_status = cache_status
        self.calls = []

    def connect(self, *, defer_session_cache=False):
        self.calls.append(("connect", defer_session_cache))
        if self.auth_error:
            raise self.auth_error
        return self

    def account_numbers(self):
        self.calls.append(("account_numbers",))
        return self.accounts

    def select_account(self, expected):
        self.calls.append(("select_account", expected))
        return expected

    def persist_session_cache_and_verify(self):
        self.calls.append(("persist_session_cache_and_verify",))
        return self.cache_status

    def get_balances(self, *_args):
        raise AssertionError("auth-only refresh must not read balances")

    def get_positions(self, *_args):
        raise AssertionError("auth-only refresh must not read positions")

    def close(self):
        self.calls.append(("close",))


def _refresh_credentials(**kwargs):
    values = {
        "username": "synthetic-user",
        "password": "synthetic-password",
        "reuse_session": True,
        "persist_session_cache": True,
        "gcs_state_bucket": "synthetic-cache-bucket",
    }
    values.update(kwargs)
    return FirstradeCredentials(**values)


def test_target_bound_session_refresh_checks_target_before_constructing_client():
    from types import SimpleNamespace

    constructions = []
    runtime_target = SimpleNamespace(account_selector=("first", "second"))
    result = run_target_bound_account_session_refresh(
        runtime_target=runtime_target,
        credentials=_refresh_credentials(),
        client_factory=lambda *_args, **_kwargs: constructions.append(True),
    )
    assert result["status"] == "runtime_target_invalid"
    assert constructions == []


def test_target_bound_session_refresh_rejects_legacy_account_conflict_before_credentials():
    from types import SimpleNamespace

    env_reads = []
    result = run_target_bound_account_session_refresh(
        runtime_target=SimpleNamespace(account_selector=("expected-placeholder",)),
        env_reader=lambda name, default=None: env_reads.append(name) or "other-placeholder",
        client_factory=lambda *_args, **_kwargs: pytest.fail("client must not be constructed"),
    )
    assert result["status"] == "configured_account_conflict"
    assert env_reads == ["FIRSTRADE_ACCOUNT"]


def test_target_bound_session_refresh_saves_only_after_exact_native_identity():
    from types import SimpleNamespace

    client = None

    def factory(credentials, **kwargs):
        nonlocal client
        assert credentials.debug is False
        assert kwargs["live_trading_enabled"] is False
        client = AuthRefreshClient(credentials, **kwargs)
        return client

    result = run_target_bound_account_session_refresh(
        runtime_target=SimpleNamespace(account_selector=("synthetic-account-placeholder",)),
        credentials=_refresh_credentials(debug=True),
        client_factory=factory,
        env_reader=_env({}),
    )
    assert result == {
        "status": "ok",
        "runtime_target_valid": True,
        "credentials_available": True,
        "session_connected": True,
        "account_match": True,
        "cache_persisted": True,
        "cache_readback_fresh": True,
    }
    assert client.calls == [
        ("connect", True),
        ("account_numbers",),
        ("select_account", "synthetic-account-placeholder"),
        ("persist_session_cache_and_verify",),
        ("close",),
    ]


@pytest.mark.parametrize(
    "accounts",
    [
        ["synthetic-other-placeholder"],
        ["synthetic-account-placeholder", "synthetic-account-placeholder"],
        ["synthetic-account-placeholder", "synthetic-other-placeholder"],
        [],
    ],
)
def test_target_bound_session_refresh_mismatch_never_persists_cache(accounts):
    from types import SimpleNamespace

    client = None

    def factory(credentials, **kwargs):
        nonlocal client
        client = AuthRefreshClient(
            credentials, accounts=accounts, **kwargs
        )
        return client

    result = run_target_bound_account_session_refresh(
        runtime_target=SimpleNamespace(account_selector=("synthetic-account-placeholder",)),
        credentials=_refresh_credentials(),
        client_factory=factory,
        env_reader=_env({}),
    )
    assert result["status"] == "runtime_target_account_mismatch"
    assert result["account_match"] is False
    assert not any(call[0] == "persist_session_cache_and_verify" for call in client.calls)
    assert client.calls[-1] == ("close",)


@pytest.mark.parametrize(
    "error,expected_status",
    [
        (RuntimeError("synthetic private auth error"), "session_authentication_failed"),
        (
            FirstradeMfaRequired("synthetic mfa detail"),
            "mfa_required",
        ),
    ],
)
def test_target_bound_session_refresh_auth_failure_is_not_retried_or_persisted(error, expected_status):
    from types import SimpleNamespace

    client = None

    def factory(credentials, **kwargs):
        nonlocal client
        client = AuthRefreshClient(credentials, auth_error=error, **kwargs)
        return client

    result = run_target_bound_account_session_refresh(
        runtime_target=SimpleNamespace(account_selector=("synthetic-account-placeholder",)),
        credentials=_refresh_credentials(),
        client_factory=factory,
        env_reader=_env({}),
    )
    assert result["status"] == expected_status
    assert client.calls == [("connect", True), ("close",)]
    assert "synthetic private auth error" not in str(result)
    assert "synthetic mfa detail" not in str(result)


@pytest.mark.parametrize(
    "cache_status,expected_status,readback_fresh",
    [
        ("cache_persist_failed", "session_cache_persist_failed", False),
        ("cache_readback_failed", "session_cache_readback_failed", False),
    ],
)
def test_target_bound_session_refresh_does_not_report_unverified_cache_as_success(
    cache_status, expected_status, readback_fresh
):
    from types import SimpleNamespace

    result = run_target_bound_account_session_refresh(
        runtime_target=SimpleNamespace(account_selector=("synthetic-account-placeholder",)),
        credentials=_refresh_credentials(),
        client_factory=lambda credentials, **kwargs: AuthRefreshClient(
            credentials, cache_status=cache_status, **kwargs
        ),
        env_reader=_env({}),
    )
    assert result["status"] == expected_status
    assert result["cache_readback_fresh"] is readback_fresh
    assert result["account_match"] is True


def test_target_bound_session_refresh_requires_existing_durable_cache_configuration():
    from types import SimpleNamespace

    clients = []
    result = run_target_bound_account_session_refresh(
        runtime_target=SimpleNamespace(account_selector=("synthetic-account-placeholder",)),
        credentials=_refresh_credentials(persist_session_cache=False),
        client_factory=lambda *_args, **_kwargs: clients.append(True),
        env_reader=_env({}),
    )
    assert result["status"] == "session_persistence_not_enabled"
    assert clients == []


def test_target_bound_session_refresh_does_not_request_new_otp_without_existing_code():
    from types import SimpleNamespace

    observed = {}

    def factory(credentials, **kwargs):
        observed["email"] = credentials.email
        observed["phone"] = credentials.phone
        return AuthRefreshClient(
            credentials, auth_error=FirstradeMfaRequired("synthetic challenge"), **kwargs
        )

    result = run_target_bound_account_session_refresh(
        runtime_target=SimpleNamespace(account_selector=("synthetic-account-placeholder",)),
        credentials=_refresh_credentials(email="private@example.invalid", phone="5550100"),
        client_factory=factory,
        env_reader=_env({}),
    )

    assert result["status"] == "mfa_required"
    assert observed == {"email": "", "phone": ""}


def test_cached_balance_field_diagnostic_uses_exact_runtime_target_and_returns_schema_only():
    from types import SimpleNamespace

    client = None

    def client_factory(credentials, *, live_trading_enabled):
        nonlocal client
        assert credentials.reuse_session is True
        client = DiagnosticClient(credentials, live_trading_enabled=live_trading_enabled)
        return client

    result = run_cached_balance_field_diagnostic(
        runtime_target=SimpleNamespace(account_selector=("synthetic-account-placeholder",)),
        credentials=FirstradeCredentials(
            username="synthetic-user", password="", reuse_session=True
        ),
        client_factory=client_factory,
    )

    assert result["status"] == "ok"
    assert result["cached_session_fresh"] is True
    assert result["account_match"] is True
    assert result["read_only_session_accepted"] is True
    assert result["source"] == "firstrade_private_balances"
    assert {entry["label"] for entry in result["recognized_display_labels"]} == {"cash_balance"}
    assert result["unit_field_paths"] == ["cash.currency"]
    assert any(entry == {"path": "available", "type": "string"} for entry in result["fields"])
    assert result["unrecognized_fields_omitted"] is True
    assert result["unrecognized_field_count"] == 4
    serialized = str(result)
    assert "DO_NOT_RETURN" not in serialized
    assert "synthetic-account-placeholder" not in serialized
    assert "12345678" not in serialized
    assert "AliceTrading" not in serialized
    assert "A1" not in serialized
    assert "DO_NOT_RETURN_DYNAMIC" not in serialized
    assert client is not None
    assert client.calls == [
        "cache_check",
        "connect_read_only",
        "account_numbers",
        "select_account",
        "close",
    ]
    assert client.account_data.balance_reads == ["synthetic-account-placeholder"]


def test_cached_balance_field_diagnostic_fails_closed_for_missing_cache_or_target():
    from types import SimpleNamespace

    client = None

    def client_factory(credentials, *, live_trading_enabled):
        nonlocal client
        client = DiagnosticClient(credentials, live_trading_enabled=live_trading_enabled)
        client.has_fresh_cached_session = lambda: False
        return client

    missing_cache = run_cached_balance_field_diagnostic(
        runtime_target=SimpleNamespace(account_selector=("synthetic-account-placeholder",)),
        credentials=FirstradeCredentials(
            username="synthetic-user", password="", reuse_session=True
        ),
        client_factory=client_factory,
    )
    invalid_target = run_cached_balance_field_diagnostic(
        runtime_target=SimpleNamespace(account_selector=("first", "second")),
        credentials=FirstradeCredentials(
            username="synthetic-user", password="", reuse_session=True
        ),
        client_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid selector must reject before client construction")
        ),
    )

    assert missing_cache == {
        "status": "cached_session_missing_or_stale",
        "cached_session_fresh": False,
    }
    assert invalid_target == {"status": "runtime_target_invalid", "cached_session_fresh": False}
    assert client is not None
    assert client.calls == ["close"]


def test_cached_balance_field_diagnostic_rejects_nonunique_runtime_target_account():
    from types import SimpleNamespace

    class DuplicateAccountClient(DiagnosticClient):
        def account_numbers(self):
            self.calls.append("account_numbers")
            return ["synthetic-account-placeholder", "synthetic-account-placeholder"]

    client = None

    def client_factory(credentials, *, live_trading_enabled):
        nonlocal client
        client = DuplicateAccountClient(credentials, live_trading_enabled=live_trading_enabled)
        return client

    result = run_cached_balance_field_diagnostic(
        runtime_target=SimpleNamespace(account_selector=("synthetic-account-placeholder",)),
        credentials=FirstradeCredentials(
            username="synthetic-user", password="", reuse_session=True
        ),
        client_factory=client_factory,
    )

    assert result == {
        "status": "runtime_target_account_mismatch",
        "cached_session_fresh": True,
        "read_only_session_accepted": True,
        "account_match": False,
    }
    assert client is not None
    assert "select_account" not in client.calls
    assert client.account_data.balance_reads == []


def test_build_account_funds_snapshot_uses_full_account_and_compacts_values():
    snapshot = build_account_funds_snapshot(
        account="12345678",
        account_summaries=[{"account": "****5678", "total_value": "100.00"}],
        balances={"total_account_value": "100.00", "cash_balance": "40.00", "note": "x"},
        positions_payload={"items": [{"symbol": "SPY", "quantity": "2", "market_value": "900.50"}]},
        session_reused=True,
        now=datetime(2026, 5, 23, 1, 2, 3, tzinfo=timezone.utc),
    )

    assert snapshot["account"] == "12345678"
    assert snapshot["session_reused"] is True
    assert snapshot["balance_metrics"] == {
        "total_account_value": 100.0,
        "cash_balance": 40.0,
    }
    assert snapshot["positions"] == [
        {"symbol": "SPY", "quantity": 2.0, "market_value": 900.5}
    ]


def test_run_session_check_persists_funds_snapshot_when_enabled():
    store = FakeStateStore()
    now = datetime(2026, 5, 23, 1, 2, 3, tzinfo=timezone.utc)

    result = run_session_check(
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=FakeClient,
        state_store=store,
        env_reader=lambda name, default=None: {
            "FIRSTRADE_PERSIST_ACCOUNT_SNAPSHOT": "true",
            "FIRSTRADE_SESSION_CHECK_INCLUDE_POSITIONS": "true",
        }.get(name, default),
        now=now,
    )

    assert result["ok"] is True
    assert result["session_reused"] is True
    assert result["snapshot_persisted"] is True
    assert len(store.writes) == 2
    assert store.writes[0][0] == "accounts/12345678/funds/latest.json"
    assert store.writes[1][0] == "accounts/12345678/funds/history/2026/05/23/20260523T010203Z.json"
    assert store.writes[0][1]["positions"][0]["symbol"] == "SPY"


def test_monthly_session_check_skips_when_current_period_is_already_maintained():
    now = datetime(2026, 6, 3, 1, 2, 3, tzinfo=timezone.utc)
    state_key = (
        "session-checks/auto/russell_top50_leader_rotation/2026_06/latest.json"
    )
    store = FakeStateStore(
        {
            state_key: {
                "checked_at": "2026-06-01T01:02:03+00:00",
                "period": "2026-06",
            }
        }
    )

    result = run_session_check(
        client_factory=ExplodingClient,
        state_store=store,
        env_reader=_env({"STRATEGY_PROFILE": "russell_top50_leader_rotation"}),
        now=now,
    )

    assert result["ok"] is True
    assert result["session_check_skipped"] is True
    assert result["session_check_policy"] == "auto"
    assert result["session_check_period"] == "2026-06"
    assert result["session_check_last_checked_at"] == "2026-06-01T01:02:03+00:00"
    assert store.reads == [state_key]
    assert store.writes == []


def test_monthly_session_check_runs_and_persists_maintenance_state_when_due():
    now = datetime(2026, 6, 3, 1, 2, 3, tzinfo=timezone.utc)
    store = FakeStateStore()

    result = run_session_check(
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=FakeClient,
        state_store=store,
        env_reader=_env({"STRATEGY_PROFILE": "russell_top50_leader_rotation"}),
        now=now,
    )

    assert result["ok"] is True
    assert result["session_check_maintenance_state_persisted"] is True
    state_key = (
        "session-checks/auto/russell_top50_leader_rotation/2026_06/latest.json"
    )
    assert store.reads == [state_key]
    assert store.writes == [
        (
            state_key,
            {
                "checked_at": "2026-06-03T01:02:03+00:00",
                "account": "12345678",
                "session_reused": True,
                "strategy_profile": "russell_top50_leader_rotation",
                "strategy_cadence": "monthly",
                "strategy_required_inputs": ["feature_snapshot"],
                "period": "2026-06",
                "policy": "auto",
            },
        )
    ]


def test_daily_session_check_runs_every_time_without_maintenance_state_lookup():
    now = datetime(2026, 6, 3, 1, 2, 3, tzinfo=timezone.utc)
    store = FakeStateStore()

    result = run_session_check(
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=FakeClient,
        state_store=store,
        env_reader=_env({"STRATEGY_PROFILE": "tqqq_growth_income"}),
        now=now,
    )

    assert result["ok"] is True
    assert result["session_check_policy_reason"] == "daily_strategy"
    assert result["session_check_maintenance_state_persisted"] is False
    assert store.reads == []
    assert store.writes == []


def test_session_check_policy_always_overrides_monthly_throttle():
    now = datetime(2026, 6, 3, 1, 2, 3, tzinfo=timezone.utc)
    state_key = (
        "session-checks/auto/russell_top50_leader_rotation/2026_06/latest.json"
    )
    store = FakeStateStore({state_key: {"checked_at": "2026-06-01T01:02:03+00:00"}})

    result = run_session_check(
        credentials=FirstradeCredentials(username="user", password="pass"),
        client_factory=FakeClient,
        state_store=store,
        env_reader=_env(
            {
                "STRATEGY_PROFILE": "russell_top50_leader_rotation",
                "FIRSTRADE_SESSION_CHECK_POLICY": "always",
            }
        ),
        now=now,
    )

    assert result["ok"] is True
    assert result["session_check_policy"] == "always"
    assert result["session_check_policy_reason"] == "policy_always"
    assert result["session_check_maintenance_state_persisted"] is False
    assert store.reads == []
    assert store.writes == []


def test_session_check_policy_skip_does_not_require_credentials_or_client():
    result = run_session_check(
        client_factory=ExplodingClient,
        env_reader=_env({"FIRSTRADE_SESSION_CHECK_POLICY": "skip"}),
        now=datetime(2026, 6, 3, 1, 2, 3, tzinfo=timezone.utc),
    )

    assert result["ok"] is True
    assert result["session_check_skipped"] is True
    assert result["session_check_policy"] == "skip"
    assert result["session_check_policy_reason"] == "policy_skip"
