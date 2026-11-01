from __future__ import annotations

import pytest

from application import session_check_service
from application.firstrade_client import FirstradeCredentials, FirstradeMfaRequired, FirstradeSafetyError
from application.session_check_service import run_forced_session_renewal

ORDER_METHODS = ("place_stock_order", "place_order", "submit_order", "cancel_order", "preview_order")
READ_METHODS = {"connect", "select_account", "get_balances", "save_verified_session_cache"}


def _credentials(**overrides):
    values = dict(
        username="u", password="p", reuse_session=True, persist_session_cache=True,
        gcs_state_bucket="bucket",
    )
    values.update(overrides)
    return FirstradeCredentials(**values)


class _RecordingClient:
    def __init__(self, credentials, *, live_trading_enabled):
        self.credentials = credentials
        self.live_trading_enabled = live_trading_enabled
        self.session_reused = False
        self.calls = []

    def __getattr__(self, name):
        if name in ORDER_METHODS:
            raise AssertionError(f"order method reached: {name}")
        raise AttributeError(name)

    def connect(self, *, force_login=False, defer_cache_save=False):
        self.calls.append(("connect", (force_login, defer_cache_save)))
        return self

    def select_account(self, requested=None):
        self.calls.append(("select_account", requested))
        return "12345979"

    def get_balances(self, account):
        self.calls.append(("get_balances", account))
        return {"cash": 1}

    def save_verified_session_cache(self):
        self.calls.append(("save_verified_session_cache", None))


def _factory(created):
    def make(credentials, *, live_trading_enabled):
        client = _RecordingClient(credentials, live_trading_enabled=live_trading_enabled)
        created.append(client)
        return client

    return make


def test_forced_renewal_logs_in_once_read_only(capsys):
    created = []
    result = run_forced_session_renewal(
        credentials=_credentials(), client_factory=_factory(created), env_reader=lambda *_: None,
    )
    assert result["ok"] is True and result["session_renewed"] is True
    assert len(created) == 1
    client = created[0]
    assert client.live_trading_enabled is False
    assert client.calls[0] == ("connect", (True, True))
    assert [call[0] for call in client.calls] == ["connect", "select_account", "get_balances", "save_verified_session_cache"]
    assert {name for name, _ in client.calls} <= READ_METHODS
    assert result["account"] == "******79"
    log = capsys.readouterr().out
    assert "account=******79" in log
    for value in ("12345979", "5979"):
        assert value not in str(result)
        assert value not in log


def test_forced_renewal_never_touches_monthly_marker(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("marker path reached")

    monkeypatch.setattr(session_check_service, "resolve_session_check_maintenance_decision", boom)
    monkeypatch.setattr(session_check_service, "persist_session_check_maintenance", boom)
    monkeypatch.setattr(session_check_service, "build_gcs_state_store_from_env", boom)
    created = []
    run_forced_session_renewal(
        credentials=_credentials(), client_factory=_factory(created), env_reader=lambda *_: None,
    )


@pytest.mark.parametrize("overrides", [{"reuse_session": False}, {"persist_session_cache": False}])
def test_forced_renewal_requires_cache_persistence(overrides):
    with pytest.raises(FirstradeSafetyError):
        run_forced_session_renewal(
            credentials=_credentials(**overrides), client_factory=_factory([]), env_reader=lambda *_: None,
        )


def test_forced_renewal_refuses_live_client():
    def make(credentials, *, live_trading_enabled):
        return _RecordingClient(credentials, live_trading_enabled=True)

    with pytest.raises(FirstradeSafetyError):
        run_forced_session_renewal(credentials=_credentials(), client_factory=make, env_reader=lambda *_: None)


def test_connect_force_login_skips_cached_session(tmp_path):
    from application.firstrade_client import FirstradeBrokerClient

    events = []

    class _Session:
        def __init__(self, **_kw):
            self.session = type("T", (), {"headers": {}, "cookies": None})()

        def login(self):
            events.append("login")
            return False

    client = FirstradeBrokerClient(
        _credentials(cookie_dir=str(tmp_path), gcs_state_bucket=""),
        live_trading_enabled=False,
        session_factory=_Session,
        account_data_factory=lambda session: object(),
        order_factory=lambda *_a: (_ for _ in ()).throw(AssertionError("order factory reached")),
    )
    client._try_cached_session = lambda *a, **k: events.append("cached") or True
    client._save_session_cache = lambda *_args: events.append("saved")
    client.connect(force_login=True)
    assert events == ["login", "saved"]


@pytest.mark.parametrize("scenario", ["success", "auto_single", "empty_balances", "wrong_account", "auto_multiple", "balances_fail", "store_false", "store_error"])
@pytest.mark.parametrize("invalid_token", [None, ("access-token", None), ("access-token", 123), ("access-token", "  "), ("ftat", None), ("ftat", 123), ("ftat", "  "), ("sid", None), ("sid", 123), ("sid", "  ")])
def test_renewal_commits_shared_cache_only_after_account_and_balance_validation(tmp_path, scenario, invalid_token):
    from application.firstrade_client import FirstradeBrokerClient, FirstradePlatformError

    events = []

    class Store:
        def write_json(self, key, value):
            events.append("write")
            if scenario == "store_error":
                raise RuntimeError("private storage error")
            return scenario != "store_false"

    class Session:
        def __init__(self, **_kwargs):
            headers = {"access-token": "synthetic", "ftat": "synthetic", "sid": "synthetic"}
            if invalid_token:
                headers[invalid_token[0]] = invalid_token[1]
            self.session = type("Transport", (), {"headers": headers, "cookies": None})()

        def login(self):
            events.append("login")
            return False

    class Data:
        account_numbers = ["12345678", "87654321"] if scenario == "auto_multiple" else ["12345678"]
        account_balances = {}

        def __init__(self, _session):
            pass

        def get_account_balances(self, _account):
            events.append("balances")
            if scenario == "balances_fail":
                raise FirstradePlatformError("synthetic failure")
            if scenario == "empty_balances":
                return {}
            return {"synthetic": 1}

    def factory(credentials, *, live_trading_enabled):
        return FirstradeBrokerClient(credentials, live_trading_enabled=live_trading_enabled,
            session_factory=Session, account_data_factory=Data, session_cache_store=Store())

    requested = "" if scenario in {"auto_single", "auto_multiple"} else "87654321" if scenario == "wrong_account" else "12345678"
    def run():
        return run_forced_session_renewal(credentials=_credentials(cookie_dir=str(tmp_path)), client_factory=factory,
            env_reader=lambda key, default=None: requested if key == "FIRSTRADE_ACCOUNT" else default)

    if scenario in {"success", "auto_single", "empty_balances"} and invalid_token is None:
        result = run()
        assert result["session_renewed"] is True
        assert result["balances_read"] is (scenario != "empty_balances")
        assert events == ["login", "balances", "write"]
    else:
        with pytest.raises(FirstradePlatformError) as error:
            run()
        assert "private storage error" not in str(error.value)
        assert events.count("write") == (1 if scenario in {"store_false", "store_error"} and invalid_token is None else 0)


# --- HTTP route ---------------------------------------------------------------------------


@pytest.fixture
def http_client(monkeypatch):
    pytest.importorskip("flask")
    import main

    def no_orders(*_a, **_k):
        raise AssertionError("strategy/order path reached")

    monkeypatch.setattr(main, "run_strategy_cycle", no_orders)
    monkeypatch.setattr(main, "run_session_check", no_orders)
    monkeypatch.setattr(main, "_run_strategy_cycle_with_report", no_orders)
    from unittest.mock import Mock
    from application.session_renewal_guard import run_guarded_renewal
    store = Mock()
    store.read.return_value = (None, 0)
    store.write.return_value = 1
    store.claim_alert.return_value = True
    monkeypatch.setattr(main, "run_guarded_renewal", lambda renew, **kwargs: run_guarded_renewal(
        renew, store=store, sender=lambda *_: True, **kwargs,
    ))
    return main


def test_route_disabled_without_http_flag(http_client, monkeypatch):
    monkeypatch.delenv("FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP", raising=False)
    response = http_client.app.test_client().post("/session-renew")
    assert response.status_code == 403


def test_route_runs_forced_renewal_only(http_client, monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP", "true")
    calls = []
    monkeypatch.setattr(http_client, "run_forced_session_renewal", lambda: calls.append(1) or {"ok": True, "session_renewed": True})
    response = http_client.app.test_client().post("/session-renew")
    assert response.status_code == 200 and calls == [1]


def test_route_reports_mfa_required(http_client, monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP", "true")

    def mfa():
        raise FirstradeMfaRequired("mfa")

    monkeypatch.setattr(http_client, "run_forced_session_renewal", mfa)
    response = http_client.app.test_client().post("/session-renew")
    assert response.status_code == 409
    assert response.get_json()["error"] == "mfa_or_device_verification_required"


def test_route_is_post_only(http_client):
    response = http_client.app.test_client().get("/session-renew")
    assert response.status_code == 405


def test_renewal_does_not_read_mfa_secret_or_configuration(monkeypatch):
    from quant_platform_kit import cloud
    requested = []

    class SecretStore:
        def get_secret(self, name, **kwargs):
            assert name in {"firstrade-username", "firstrade-password"}
            requested.append(name)
            return "synthetic"

    def env(name, default=None):
        assert not any(part in name for part in ("MFA", "PIN"))
        return {"FIRSTRADE_REUSE_SESSION": "true", "FIRSTRADE_PERSIST_SESSION_CACHE": "true"}.get(name, default)

    monkeypatch.setattr(cloud, "get_secret_store", lambda: SecretStore())
    created = []
    run_forced_session_renewal(client_factory=_factory(created), env_reader=env)
    assert requested == ["firstrade-username", "firstrade-password"]
    assert not created[0].credentials.mfa_secret


def test_renewal_strips_injected_mfa_material_and_debug():
    created = []
    run_forced_session_renewal(credentials=_credentials(pin="synthetic", email="x@example.invalid",
        phone="synthetic", mfa_secret="synthetic", mfa_code="synthetic", debug=True),
        client_factory=_factory(created), env_reader=lambda *_: None)
    creds = created[0].credentials
    assert not any((creds.pin, creds.email, creds.phone, creds.mfa_secret, creds.mfa_code, creds.debug))


@pytest.mark.parametrize("sdk_error,reason", [
    ("LoginError", "mfa"), ("LoginResponseError", "auth_rejected"),
    ("LoginRequestError", "auth_rejected"),
])
def test_sdk_challenges_never_retry_or_read_accounts(sdk_error, reason):
    from firstrade import exceptions
    from application.session_check_service import SessionRenewalFailure
    calls = []
    class Client(_RecordingClient):
        def connect(self, **kwargs):
            calls.append("login")
            error = getattr(exceptions, sdk_error)
            raise error(403 if sdk_error == "LoginRequestError" else "private-synthetic")
        def select_account(self, requested=None):
            pytest.fail("account read after auth challenge")
    with pytest.raises(FirstradeMfaRequired if reason == "mfa" else SessionRenewalFailure) as caught:
        run_forced_session_renewal(credentials=_credentials(), client_factory=Client)
    assert calls == ["login"] and "private-synthetic" not in str(caught.value)


def test_deadline_http_route_never_reaches_broker(http_client, monkeypatch):
    from application import session_renewal_guard as guard
    from datetime import datetime, timezone
    from unittest.mock import Mock
    def no_broker(*args, **kwargs):
        pytest.fail("broker or credentials reached")
    monkeypatch.setattr(http_client, "run_forced_session_renewal", no_broker)
    monkeypatch.setattr(FirstradeCredentials, "from_env", no_broker)
    store = Mock()
    store.read.return_value = (None, 0)
    store.claim_alert.return_value = True
    monkeypatch.setattr(http_client, "check_renewal_deadline", lambda **kwargs: guard.check_renewal_deadline(
        store=store, sender=lambda *_: True, now=datetime(2026, 1, 2, 21, 15, tzinfo=timezone.utc), **kwargs))
    response = http_client.app.test_client().post("/session-renew-deadline")
    assert response.status_code == 200
    assert response.get_json()["error"] == "not_completed_by_2115_utc"


@pytest.mark.parametrize("challenge", ["mfa", "new_device"])
def test_real_sdk_challenge_boundary_never_submits_second_factor(tmp_path, challenge):
    from firstrade.account import FTSession
    from application.firstrade_client import FirstradeBrokerClient
    from types import SimpleNamespace
    calls = []
    class Session(FTSession):
        def _load_cookies(self):
            return ""
        def _request(self, method, url, **kwargs):
            calls.append(method)
            assert len(calls) <= 2, "SDK attempted MFA, OTP or login retry"
            payload = {"error": "", "mfa": challenge == "mfa", "t_token": "synthetic",
                       "otp": [{"channel": "sms", "recipientMask": "synthetic"}]}
            return SimpleNamespace(status_code=200, json=lambda: payload)
    def no_accounts(_):
        pytest.fail("account data reached after challenge")
    def client(credentials, *, live_trading_enabled):
        return FirstradeBrokerClient(credentials, live_trading_enabled=False,
            session_factory=Session, account_data_factory=no_accounts)
    with pytest.raises(FirstradeMfaRequired):
        run_forced_session_renewal(credentials=_credentials(cookie_dir=str(tmp_path),
            mfa_secret="synthetic", mfa_code="synthetic", phone="synthetic", pin="synthetic"),
            client_factory=client)
    assert calls == ["get", "post"]


@pytest.mark.parametrize("missing_field", ["username", "password"])
def test_missing_credentials_alert_without_constructing_broker(missing_field):
    from unittest.mock import Mock
    from application.session_renewal_guard import run_guarded_renewal
    store, sender = Mock(), Mock(return_value=True)
    store.read.return_value = (None, 0)
    store.write.return_value = 1
    store.claim_alert.return_value = True
    credentials = _credentials(**{missing_field: ""})
    def factory(*args, **kwargs):
        pytest.fail("broker constructed without credentials")
    result, status = run_guarded_renewal(
        lambda: run_forced_session_renewal(credentials=credentials, client_factory=factory),
        store=store, sender=sender,
    )
    assert status == 503
    assert result["error"] == "credentials_unavailable"
    assert result["stage"] == "credentials"
    assert result["manual_recovery_required"] is True
    failed = store.write.call_args_list[-1].args[0]
    assert failed["status"] == "manual_required"
    assert sender.call_args.args[2:] == ("credentials_unavailable", "credentials")
