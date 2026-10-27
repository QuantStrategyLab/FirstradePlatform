from __future__ import annotations

from types import SimpleNamespace

import pytest

from application.firstrade_client import (
    FirstradeBrokerClient,
    FirstradeCachedSessionError,
    FirstradeCredentials,
    FirstradePlatformError,
    FirstradeSafetyError,
    StockOrderRequest,
    mask_account_id,
    validate_stock_order,
)


class FakeSession:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.login_two_code = None
        self.session = SimpleNamespace(headers={})

    def login(self):
        self.session.headers["ftat"] = "fake-ftat"
        self.session.headers["sid"] = "fake-sid"
        return False

    def login_two(self, code):
        self.login_two_code = code


class FakeAccountData:
    account_numbers = ["12345678"]
    account_balances = {"12345678": "100.00"}

    def __init__(self, _session):
        pass

    def get_account_balances(self, account):
        return {"account": account, "total_value": "100.00", "cash": "80.00"}

    def get_positions(self, _account):
        return {"items": [{"symbol": "SPY", "quantity": "1", "market_value": "500"}]}

    def get_orders(self, _account, per_page=0):
        del per_page
        return []


class FakeOrder:
    def __init__(self, _session):
        pass

    def place_order(self, **kwargs):
        return {
            "error": "",
            "preview": kwargs["dry_run"],
            "symbol": kwargs["symbol"],
            "notional": kwargs["notional"],
            "quantity": kwargs["quantity"],
            "price": kwargs["price"],
        }


class ReusableFakeSession(FakeSession):
    login_calls = 0

    def login(self):
        type(self).login_calls += 1
        return super().login()


class HeaderCheckingAccountData(FakeAccountData):
    def __init__(self, session):
        headers = session.session.headers
        if not headers.get("ftat") or not headers.get("sid"):
            raise RuntimeError("missing cached auth headers")


def build_fake_client(live=False):
    credentials = FirstradeCredentials(username="user", password="pass")
    return FirstradeBrokerClient(
        credentials,
        live_trading_enabled=live,
        session_factory=FakeSession,
        account_data_factory=FakeAccountData,
        order_factory=FakeOrder,
    ).connect()


def test_mask_account_id_keeps_only_last_four_digits():
    assert mask_account_id("12345678") == "****5678"


def test_notional_live_order_rejects_size_above_local_cap():
    request = StockOrderRequest(
        account="12345678",
        symbol="SPY",
        side="buy",
        notional_usd=30,
        max_notional_usd=25,
    )
    with pytest.raises(FirstradeSafetyError, match="exceeds"):
        validate_stock_order(
            request,
            dry_run=False,
            live_trading_enabled=True,
            explicit_live_ack=True,
        )


def test_live_order_has_no_default_notional_cap_when_unset():
    request = StockOrderRequest(
        account="12345678",
        symbol="SPY",
        side="buy",
        quantity=10,
        price_type="limit",
        limit_price=100,
    )

    validate_stock_order(
        request,
        dry_run=False,
        live_trading_enabled=True,
        explicit_live_ack=True,
    )


def test_live_order_requires_environment_gate_and_ack():
    request = StockOrderRequest(
        account="12345678",
        symbol="SPY",
        side="buy",
        notional_usd=5,
    )
    with pytest.raises(FirstradeSafetyError, match="FIRSTRADE_ENABLE_LIVE_TRADING"):
        validate_stock_order(request, dry_run=False, live_trading_enabled=False)
    with pytest.raises(FirstradeSafetyError, match="unofficial"):
        validate_stock_order(request, dry_run=False, live_trading_enabled=True)


def test_client_order_preview_uses_dry_run_by_default():
    client = build_fake_client()
    response = client.place_stock_order(
        StockOrderRequest(
            account="12345678",
            symbol="SPY",
            side="buy",
            notional_usd=5,
        )
    )
    assert response["preview"] is True
    assert response["notional"] is True
    assert response["price"] == 5.0


def test_get_order_status_normalizes_matching_order_payload():
    class OrdersAccountData(FakeAccountData):
        def get_orders(self, _account, per_page=0):
            del per_page
            return [
                {
                    "order_id": "OID-123",
                    "status": "Filled",
                    "filled_quantity": "3",
                    "avg_price": "101.25",
                }
            ]

    credentials = FirstradeCredentials(username="user", password="pass")
    client = FirstradeBrokerClient(
        credentials,
        session_factory=FakeSession,
        account_data_factory=OrdersAccountData,
        order_factory=FakeOrder,
    ).connect()

    status = client.get_order_status("12345678", "OID-123")

    assert status == {
        "status": "Filled",
        "executed_qty": 3.0,
        "executed_price": 101.25,
        "broker_order_id": "OID-123",
        "raw_payload": {
            "order_id": "OID-123",
            "status": "Filled",
            "filled_quantity": "3",
            "avg_price": "101.25",
        },
    }


@pytest.mark.parametrize("reverse_fields", [False, True])
@pytest.mark.parametrize("fields,expected_qty,expected_price", [
    ({"quantity": 10, "limit_price": 9.95}, 0.0, 0.0),
    ({"quantity": 10, "limit_price": 9.95, "executed_qty": 0, "executed_price": 0,
      "filled_quantity": 8, "avg_price": 99}, 0.0, 0.0),
    ({"quantity": 10, "limit_price": 9.95, "executed_qty": None, "executed_price": None,
      "filled_quantity": 3, "avg_price": 10}, 3.0, 10.0),
    ({"quantity": 10, "limit_price": 9.95, "executed_qty": 2, "executed_price": 10,
      "filled_quantity": 8, "avg_price": 99}, 2.0, 10.0),
    ({"quantity": 10, "limit_price": 9.95, "filled_quantity": 3, "avg_price": 10}, 3.0, 10.0),
    ({"quantity": 10, "limit_price": 9.95, "executed_qty": "invalid", "executed_price": "",
      "filled_quantity": 8, "avg_price": 99}, 0.0, 0.0),
    ({"quantity": 10, "limit_price": 9.95, "avg_price": 10}, 0.0, 10.0),
    ({"quantity": 10, "limit_price": 9.95, "filled_quantity": 3}, 3.0, 0.0),
    ({"executed_qty": [], "filled_quantity": 8, "executed_price": 10}, 0.0, 10.0),
    ({"executed_qty": 2, "executed_price": {}, "avg_price": 99}, 2.0, 0.0),
])
def test_get_order_status_uses_execution_fields_in_declared_priority(
    fields, expected_qty, expected_price, reverse_fields,
):
    from quant_platform_kit.common.order_status import compute_confirmed_sell_release_value

    items = list(fields.items())
    if reverse_fields:
        items.reverse()
    row = {"order_id": "SYNTHETIC-SELL", "status": "PartiallyFilled", **dict(items)}
    client = FirstradeBrokerClient(FirstradeCredentials(username="", password=""))
    client.session = object()
    client.account_data = SimpleNamespace(get_orders=lambda *_args, **_kwargs: [row])

    status = client.get_order_status("synthetic-account", "SYNTHETIC-SELL")

    assert status["executed_qty"] == expected_qty
    assert status["executed_price"] == expected_price
    assert status["raw_payload"] == row
    assert compute_confirmed_sell_release_value(
        submitted_sell_orders=[{"broker_order_id": "SYNTHETIC-SELL", "status": "submitted"}],
        fetch_order_status=lambda order_id: client.get_order_status("synthetic-account", order_id),
    ) == pytest.approx(expected_qty * expected_price)


def test_get_balances_includes_account_list_total_value():
    class BalancesWithoutTotalAccountData(FakeAccountData):
        account_balances = {"12345678": "$987.65"}

        def get_account_balances(self, account):
            return {"account": account, "cash_balance": "$987.65"}

    credentials = FirstradeCredentials(username="user", password="pass")
    client = FirstradeBrokerClient(
        credentials,
        session_factory=FakeSession,
        account_data_factory=BalancesWithoutTotalAccountData,
        order_factory=FakeOrder,
    ).connect()

    balances = client.get_balances("12345678")

    assert balances["account_list_total_value"] == "$987.65"


def test_select_account_requires_explicit_account_when_multiple():
    class MultiAccountData(FakeAccountData):
        account_numbers = ["11111111", "22222222"]
        account_balances = {"11111111": "1", "22222222": "2"}

    credentials = FirstradeCredentials(username="user", password="pass")
    client = FirstradeBrokerClient(
        credentials,
        session_factory=FakeSession,
        account_data_factory=MultiAccountData,
        order_factory=FakeOrder,
    ).connect()
    with pytest.raises(Exception, match="Multiple Firstrade accounts"):
        client.select_account()


def test_client_reuses_cached_session_without_logging_in_again(tmp_path):
    ReusableFakeSession.login_calls = 0
    credentials = FirstradeCredentials(
        username="user",
        password="pass",
        cookie_dir=str(tmp_path),
        reuse_session=True,
        session_cache_ttl_seconds=3600,
    )

    first_client = FirstradeBrokerClient(
        credentials,
        session_factory=ReusableFakeSession,
        account_data_factory=HeaderCheckingAccountData,
        order_factory=FakeOrder,
    ).connect()
    assert first_client.session_reused is False
    assert first_client.read_only_transport_enabled is False
    assert ReusableFakeSession.login_calls == 1

    second_client = FirstradeBrokerClient(
        credentials,
        session_factory=ReusableFakeSession,
        account_data_factory=HeaderCheckingAccountData,
        order_factory=FakeOrder,
    ).connect()

    assert second_client.session_reused is True
    assert second_client.read_only_transport_enabled is False
    assert ReusableFakeSession.login_calls == 1

    from application.account_facts_readonly import AccountFactsUnavailable, collect_firstrade_account_facts

    with pytest.raises(AccountFactsUnavailable) as error:
        collect_firstrade_account_facts(
            second_client,
            expected_account="12345678",
        )
    assert error.value.reason_code == "readonly_client_unavailable"


class FakeStateStore:
    def __init__(self):
        self.payloads = {}
        self.writes = 0

    def read_json(self, key):
        return self.payloads.get(key)

    def write_json(self, key, payload):
        self.payloads[key] = dict(payload)
        self.writes += 1
        return True


def test_client_reuses_persisted_session_cache_when_local_cache_is_missing(tmp_path):
    ReusableFakeSession.login_calls = 0
    store = FakeStateStore()
    credentials = FirstradeCredentials(
        username="user",
        password="pass",
        cookie_dir=str(tmp_path / "first"),
        reuse_session=True,
        session_cache_ttl_seconds=3600,
        persist_session_cache=True,
    )

    first_client = FirstradeBrokerClient(
        credentials,
        session_factory=ReusableFakeSession,
        account_data_factory=HeaderCheckingAccountData,
        order_factory=FakeOrder,
        session_cache_store=store,
    ).connect()
    assert first_client.session_reused is False
    assert first_client.read_only_transport_enabled is False
    assert ReusableFakeSession.login_calls == 1
    assert store.writes == 1

    second_credentials = FirstradeCredentials(
        username="user",
        password="pass",
        cookie_dir=str(tmp_path / "second"),
        reuse_session=True,
        session_cache_ttl_seconds=3600,
        persist_session_cache=True,
    )
    second_client = FirstradeBrokerClient(
        second_credentials,
        session_factory=ReusableFakeSession,
        account_data_factory=HeaderCheckingAccountData,
        order_factory=FakeOrder,
        session_cache_store=store,
    ).connect()

    assert second_client.session_reused is True
    assert second_client.read_only_transport_enabled is False
    assert ReusableFakeSession.login_calls == 1
    assert store.writes == 2


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"error": "private-provider-response"}, [None], {"items": [{}, "private-row"]}],
)
def test_order_reads_reject_incomplete_payload_instead_of_empty_success(payload):
    client = FirstradeBrokerClient(FirstradeCredentials(username="unused", password="unused"))
    client.session = object()
    client.account_data = SimpleNamespace(get_orders=lambda *_args, **_kwargs: payload)

    for read in (lambda: client.get_orders("test-account"), lambda: client.get_order_status("test-account", "test-order")):
        with pytest.raises(FirstradePlatformError) as error:
            read()
        assert str(error.value) == "Firstrade returned an invalid order response."


@pytest.mark.parametrize("wrapper", [None, "items", "orders", "data", "result"])
@pytest.mark.parametrize("rows", [[], [{"order_id": "test-order", "status": "Submitted"}]])
def test_order_reads_preserve_supported_complete_payloads(wrapper, rows):
    payload = rows if wrapper is None else {wrapper: rows}
    client = FirstradeBrokerClient(FirstradeCredentials(username="unused", password="unused"))
    client.session = object()
    client.account_data = SimpleNamespace(get_orders=lambda *_args, **_kwargs: payload)

    assert client.get_orders("test-account") == rows


def _cached_read_only_client(tmp_path, monkeypatch, *, saved_at=1000.0, account_factory=HeaderCheckingAccountData):
    import application.firstrade_client as module
    import requests

    monkeypatch.setattr(module, "time", lambda: 1001.0)
    calls = []

    class CachedSession(FakeSession):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.closed = False
            self.session = SimpleNamespace(headers={}, request=self.request, close=self.close)

        def build_session_from_tokens(self, payload):
            self.session.headers.update(payload)

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            response = requests.Response()
            response.status_code = 200
            response._content = b'{}'
            return response

        def close(self):
            self.closed = True

        def login(self):
            pytest.fail("read-only connection must never authenticate")

        def login_two(self, _code):
            pytest.fail("read-only connection must never send MFA")

    credentials = FirstradeCredentials(
        username="synthetic-user", password="", reuse_session=True,
        cookie_dir=str(tmp_path / "absent"), debug=True,
    )
    store = FakeStateStore()
    client = FirstradeBrokerClient(
        credentials, session_factory=CachedSession, account_data_factory=account_factory,
        session_cache_store=store,
    )
    store.payloads[client._session_state_key()] = {
        "saved_at": saved_at, "ftat": "synthetic-ftat", "sid": "synthetic-sid",
        "access-token": "synthetic-access",
    }
    return client, store, calls


def test_read_only_connection_reuses_cache_without_auth_or_writes(tmp_path, monkeypatch):
    client, store, calls = _cached_read_only_client(tmp_path, monkeypatch)
    assert client.connect_read_only() is client
    assert client.session_reused
    assert client.read_only_transport_enabled is True
    assert client.live_trading_enabled is False
    assert client.session.kwargs["debug"] is False
    assert client.session.kwargs["save_session"] is False
    assert not client.session.kwargs.get("password")
    assert not client.session.kwargs.get("mfa_secret")
    assert not client.session.kwargs.get("profile_path")
    assert client.session.session.trust_env is False
    assert store.writes == 0
    assert not (tmp_path / "absent").exists()
    assert calls == []
    from application.account_facts_readonly import collect_firstrade_account_facts

    facts = collect_firstrade_account_facts(client, expected_account="12345678")
    assert facts["status"] == "available"
    assert facts["account_selector_status"] == "matched"
    session = client.session
    client.close()
    assert session.closed
    assert client.session is None and client.account_data is None
    assert client.read_only_transport_enabled is False


@pytest.mark.parametrize("saved_at", [None, 0, "bad", float("nan"), float("inf"), 1002, -1, 1])
def test_read_only_connection_rejects_missing_or_expired_cache(tmp_path, monkeypatch, saved_at):
    client, store, calls = _cached_read_only_client(tmp_path, monkeypatch, saved_at=saved_at)
    from dataclasses import replace
    client.credentials = replace(client.credentials, session_cache_ttl_seconds=20)
    with pytest.raises(FirstradePlatformError, match="cached session unavailable"):
        client.connect_read_only()
    assert client.session is None and client.account_data is None
    assert not (tmp_path / "absent").exists()
    assert store.writes == 0 and calls == []


def test_read_only_failed_account_read_preserves_cache_and_closes(tmp_path, monkeypatch):
    seen = []
    def fail(session):
        seen.append(session)
        raise RuntimeError("synthetic provider error must not escape")
    client, store, calls = _cached_read_only_client(tmp_path, monkeypatch, account_factory=fail)
    original = dict(store.payloads)
    with pytest.raises(FirstradePlatformError, match="^Firstrade cached session unavailable\\.$"):
        client.connect_read_only()
    assert seen[0].closed
    assert client.session is None and client.account_data is None
    assert store.payloads == original and store.writes == 0
    assert calls == []


@pytest.mark.parametrize("method,url", [
    ("post", "https://api3x.firstrade.com/private/stock_order"),
    ("get", "https://api3x.firstrade.com/private/cancel_order"),
    ("get", "https://api3x.firstrade.com/public/quote"),
    ("get", "https://other.example/private/userinfo"),
    ("get", "http://api3x.firstrade.com/private/userinfo"),
    ("get", "https://api3x.firstrade.com/private/account_history?account=12345678"),
])
def test_read_only_transport_rejects_other_requests(tmp_path, monkeypatch, method, url):
    client, store, calls = _cached_read_only_client(tmp_path, monkeypatch)
    client.connect_read_only()
    with pytest.raises(FirstradeSafetyError):
        client.session.session.request(method, url)
    assert calls == []
    client.close()


def test_read_only_transport_is_bounded_without_redirects(tmp_path, monkeypatch):
    client, store, calls = _cached_read_only_client(tmp_path, monkeypatch)
    client.connect_read_only()
    client.session.session.request("get", "https://api3x.firstrade.com/private/userinfo")
    assert calls[0][2] == {"timeout": (5, 15), "allow_redirects": False}
    client.close()


@pytest.mark.parametrize("failure", [None, "http", "redirect", "auth", "non-json", "timeout", "network"])
def test_read_only_real_sdk_stops_on_first_read_failure(tmp_path, monkeypatch, failure):
    import json
    import requests
    from urllib.parse import urlsplit
    client, store, _ = _cached_read_only_client(tmp_path, monkeypatch)
    client._session_factory = None
    client._account_data_factory = None
    calls, closed = [], []
    original_close = requests.Session.close
    def close(session):
        closed.append(True)
        original_close(session)
    def request(session, method, url, **kwargs):
        calls.append(urlsplit(url).path)
        assert session.trust_env is False
        assert kwargs == {"timeout": (5, 15), "allow_redirects": False}
        if failure == "timeout":
            raise requests.Timeout("synthetic detail must not escape")
        if failure == "network":
            raise requests.ConnectionError("synthetic detail must not escape")
        response = requests.Response()
        response.status_code = {"http": 401, "redirect": 302}.get(failure, 200)
        body = {"error": "synthetic-denial"} if failure == "auth" else {"error": "", "items": []}
        response._content = b"not-json" if failure == "non-json" else json.dumps(body).encode()
        return response
    monkeypatch.setattr(requests.Session, "request", request)
    monkeypatch.setattr(requests.Session, "close", close)
    if failure:
        with pytest.raises(FirstradeCachedSessionError, match="^Firstrade cached session unavailable\\.$") as error:
            client.connect_read_only()
        assert error.value.reason_code == {
            "http": "read_http_error", "redirect": "read_http_error",
            "auth": "read_provider_error", "non-json": "read_invalid_payload",
            "timeout": "read_timeout", "network": "read_network_error",
        }[failure]
        assert error.value.stage == "userinfo"
        assert error.value.http_status == {"http": 401, "redirect": 302}.get(failure)
        assert "synthetic" not in str(error.value)
        assert calls == ["/private/userinfo"]
    else:
        client.connect_read_only()
        assert calls == ["/private/userinfo", "/private/acct_list"]
        client.close()
    assert closed == [True]
    assert store.writes == 0
    assert not (tmp_path / "absent").exists()


def test_cached_session_diagnostics_do_not_accept_arbitrary_provider_text():
    with pytest.raises(ValueError, match="invalid_cached_session_diagnostic"):
        FirstradeCachedSessionError("private-provider-message")


def _history_read_client(tmp_path, monkeypatch, *, history_failure=None):
    import json
    import requests
    from urllib.parse import urlsplit
    client, store, _ = _cached_read_only_client(tmp_path, monkeypatch)
    client._session_factory = None
    client._account_data_factory = None
    calls = []
    def request(session, method, url, **kwargs):
        calls.append((method, url, kwargs))
        body = {"error": "", "items": []}
        if urlsplit(url).path == "/private/acct_list":
            body["items"] = [{"account": "12345678", "total_value": "100"}]
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(body).encode()
        if urlsplit(url).path == "/private/account_history":
            if history_failure == "timeout":
                raise requests.Timeout("private detail must not escape")
            if history_failure == "http": response.status_code = 401
            if history_failure == "oversized": response._content = b"x" * (2 * 1024 * 1024 + 1)
        return response
    monkeypatch.setattr(requests.Session, "request", request)
    return client.connect_read_only(), store, calls


def test_history_read_is_one_exact_cached_get_and_never_writes(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    client, store, calls = _history_read_client(tmp_path, monkeypatch)
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=30)
    assert client.get_account_history_read_only("12345678", start.isoformat(), end.isoformat()) == {"error": "", "items": []}
    assert client.read_only_history_response_bytes == b'{"error": "", "items": []}'
    assert len(calls) == 3
    assert calls[-1] == ("get", f"https://api3x.firstrade.com/private/account_history?range=cust&range_arr[]={start}&range_arr[]={end}&page=1&account=12345678&per_page=1000", {"timeout": (5, 15), "allow_redirects": False})
    with pytest.raises(FirstradeSafetyError):
        client.get_account_history_read_only("12345678", start.isoformat(), end.isoformat())
    with pytest.raises(FirstradeSafetyError):
        client.session.session.request("get", calls[-1][1])
    assert len(calls) == 3 and store.writes == 0
    client.close()
    assert client.read_only_history_response_bytes is None


@pytest.mark.parametrize("failure", ["timeout", "http", "oversized", "extra_parameters", "duplicate_sdk_request"])
def test_history_read_stops_after_failure_without_retry_or_provider_text(tmp_path, monkeypatch, failure):
    from datetime import datetime, timedelta, timezone
    from firstrade import urls
    client, store, calls = _history_read_client(tmp_path, monkeypatch, history_failure=failure)
    end = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    if failure in {"extra_parameters", "duplicate_sdk_request"}:
        def request(account, _range, dates):
            url = urls.account_history(account, "cust", dates)
            if failure == "extra_parameters":
                return client.session.session.request("get", url, params={"account": "different"})
            client.session.session.request("get", url)
            return client.session.session.request("get", url)
        client.account_data.get_account_history = request
    with pytest.raises(FirstradeCachedSessionError, match="^Firstrade cached session unavailable\\.$"):
        client.get_account_history_read_only("12345678", end, end)
    with pytest.raises(FirstradeSafetyError):
        client.get_account_history_read_only("12345678", end, end)
    expected = 2 if failure == "extra_parameters" else 3
    assert len(calls) == client.read_only_request_attempts == expected
    assert store.writes == 0
    client.close()


@pytest.mark.parametrize("case", ["wrong_account", "query_account", "future", "too_wide", "reverse", "invalid_date", "unconnected", "live", "not_cached"])
def test_history_read_rejects_invalid_scope_before_http(tmp_path, monkeypatch, case):
    from datetime import datetime, timedelta, timezone
    client, store, calls = _history_read_client(tmp_path, monkeypatch)
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=1)
    account = "12345678"
    if case == "wrong_account": account = "different-account"
    if case == "query_account": account += "&page=2"
    if case == "future": end += timedelta(days=2)
    if case == "too_wide": start -= timedelta(days=31)
    if case == "reverse": start = end + timedelta(days=1)
    if case == "unconnected": client.close()
    if case == "live": client.live_trading_enabled = True
    if case == "not_cached": client.session_reused = False
    with pytest.raises(FirstradeSafetyError):
        client.get_account_history_read_only(account, "bad" if case == "invalid_date" else start.isoformat(), end.isoformat())
    assert len(calls) == 2 and store.writes == 0
    client.close()


def test_read_only_reports_missing_cache_before_any_provider_read(tmp_path, monkeypatch):
    client, store, calls = _cached_read_only_client(tmp_path, monkeypatch)
    store.payloads.clear()
    with pytest.raises(FirstradeCachedSessionError) as error:
        client.connect_read_only()
    assert error.value.reason_code == "cache_missing_or_stale"
    assert error.value.stage == "cache"
    assert calls == []


def test_read_only_credentials_do_not_read_password_or_mfa(monkeypatch):
    import quant_platform_kit.cloud
    reads = []
    def get_secret(name, **_kwargs):
        reads.append(name)
        assert name == "firstrade-username"
        return "synthetic-user"
    monkeypatch.setattr(quant_platform_kit.cloud, "get_secret_store", lambda: SimpleNamespace(get_secret=get_secret))
    def env(name, default=None):
        assert name not in {"FIRSTRADE_PASSWORD", "FIRSTRADE_PIN", "FIRSTRADE_MFA_SECRET", "FIRSTRADE_MFA_CODE", "FIRSTRADE_MFA_EMAIL", "FIRSTRADE_MFA_PHONE"}
        return default
    credentials = FirstradeCredentials.from_env(env, include_login_credentials=False)
    assert credentials.username == "synthetic-user"
    assert credentials.password == credentials.mfa_secret == credentials.mfa_code == ""
    assert reads == ["firstrade-username"]


def test_account_numbers_coerces_int_ids_for_selector_match():
    class IntAccountData(FakeAccountData):
        account_numbers = [12345678]
        account_balances = {12345678: "100.00"}

        def get_account_balances(self, account):
            return {"account": account, "total_value": "100.00"}

    credentials = FirstradeCredentials(username="user", password="pass")
    client = FirstradeBrokerClient(
        credentials,
        session_factory=FakeSession,
        account_data_factory=IntAccountData,
        order_factory=FakeOrder,
    ).connect()
    assert client.account_numbers() == ["12345678"]
    assert client.select_account("12345678") == "12345678"
    balances = client.get_balances("12345678")
    assert balances["account_list_total_value"] == "100.00"
