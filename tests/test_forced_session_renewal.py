from __future__ import annotations

import pytest

from application import session_check_service
from application.firstrade_client import FirstradeCredentials, FirstradeMfaRequired, FirstradeSafetyError
from application.session_check_service import run_forced_session_renewal

ORDER_METHODS = ("place_stock_order", "place_order", "submit_order", "cancel_order", "preview_order")
READ_METHODS = {"connect", "select_account", "get_balances"}


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

    def connect(self, *, force_login=False):
        self.calls.append(("connect", force_login))
        return self

    def select_account(self, requested=None):
        self.calls.append(("select_account", requested))
        return "12345979"

    def get_balances(self, account):
        self.calls.append(("get_balances", account))
        return {"cash": 1}


def _factory(created):
    def make(credentials, *, live_trading_enabled):
        client = _RecordingClient(credentials, live_trading_enabled=live_trading_enabled)
        created.append(client)
        return client

    return make


def test_forced_renewal_logs_in_once_read_only():
    created = []
    result = run_forced_session_renewal(
        credentials=_credentials(), client_factory=_factory(created), env_reader=lambda *_: None,
    )
    assert result["ok"] is True and result["session_renewed"] is True
    assert len(created) == 1
    client = created[0]
    assert client.live_trading_enabled is False
    assert client.calls[0] == ("connect", True)
    assert {name for name, _ in client.calls} <= READ_METHODS
    assert "12345979" not in str(result)


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
    client.connect(force_login=True)
    assert events == ["login"]


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
    return main


def test_route_disabled_without_http_flag(http_client, monkeypatch):
    monkeypatch.delenv("FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP", raising=False)
    response = http_client.app.test_client().post("/session-renew")
    assert response.status_code == 403


def test_route_runs_forced_renewal_only(http_client, monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP", "true")
    calls = []
    monkeypatch.setattr(http_client, "run_forced_session_renewal", lambda: calls.append(1) or {"ok": True})
    response = http_client.app.test_client().post("/session-renew")
    assert response.status_code == 200 and calls == [1]


def test_route_reports_mfa_required(http_client, monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP", "true")

    def mfa():
        raise FirstradeMfaRequired("mfa")

    monkeypatch.setattr(http_client, "run_forced_session_renewal", mfa)
    response = http_client.app.test_client().post("/session-renew")
    assert response.status_code == 409
    assert response.get_json()["error"] == "mfa_required"


def test_route_is_post_only(http_client):
    response = http_client.app.test_client().get("/session-renew")
    assert response.status_code == 405
