from __future__ import annotations

from types import SimpleNamespace

import pytest

from application.firstrade_client import (
    FirstradeBrokerClient,
    FirstradeCredentials,
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
    assert ReusableFakeSession.login_calls == 1

    second_client = FirstradeBrokerClient(
        credentials,
        session_factory=ReusableFakeSession,
        account_data_factory=HeaderCheckingAccountData,
        order_factory=FakeOrder,
    ).connect()

    assert second_client.session_reused is True
    assert ReusableFakeSession.login_calls == 1
