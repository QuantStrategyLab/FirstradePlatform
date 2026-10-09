from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from application.account_facts_readonly import (
    AccountFactsUnavailable,
    collect_firstrade_account_facts,
)


class CachedReadOnlyClient:
    session_reused = True
    read_only_transport_enabled = True
    live_trading_enabled = False
    session = object()
    account_data = object()

    def __init__(self):
        self.calls = []
        self.accounts = ["synthetic-account-a"]
        self.selected_account = "synthetic-account-a"
        self.balances = {
            "total_equity": "$1,234.50",
            "cash_balance": "$200.25",
            "buying_power": "$150.00",
            "currency": "USD",
        }
        self.positions = {
            "items": [
                {"symbol": "SPY", "quantity": "2", "market_value": "$900.00"},
                {"symbol": "AAPL", "quantity": "1", "currency": "USD"},
            ]
        }

    def account_numbers(self):
        self.calls.append("account_numbers")
        return list(self.accounts)

    def select_account(self, account):
        self.calls.append(("select_account", account))
        return self.selected_account

    def get_balances(self, account):
        self.calls.append(("get_balances", account))
        return dict(self.balances)

    def get_positions(self, account):
        self.calls.append(("get_positions", account))
        return self.positions

    def connect(self):
        pytest.fail("collector must never call a login-capable connection")


def test_collects_full_account_facts_with_distinct_balance_semantics_and_times():
    client = CachedReadOnlyClient()
    start = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    finish = start + timedelta(seconds=3)
    times = iter((start, finish))

    def clock():
        client.calls.append("clock")
        return next(times)

    result = collect_firstrade_account_facts(
        client,
        expected_account="synthetic-account-a",
        clock=clock,
    )

    assert result == {
        "status": "available",
        "platform": "firstrade",
        "account_selector_status": "matched",
        "broker_account_id": "synthetic-account-a",
        "observed_started_at": start.isoformat(),
        "observed_finished_at": finish.isoformat(),
        "balances": {
            "provider_equity": "1234.5",
            "cash_balance": "200.25",
            "available_cash": None,
            "buying_power": "150",
            "currency": "USD",
        },
        "positions_status": "available",
        "positions": [
            {"symbol": "SPY", "quantity": "2", "market_value": "900", "currency": None},
            {"symbol": "AAPL", "quantity": "1", "market_value": None, "currency": "USD"},
        ],
        "coverage": {
            "positions_requested": True,
            "positions_count": 2,
            "positions_missing_market_value": 1,
            "positions_missing_identity_fields": 0,
            "provider_equity_available": True,
            "cash_balance_available": True,
            "available_cash_available": False,
            "buying_power_available": True,
            "balance_currency_available": True,
            "positions_missing_currency": 1,
            "currency_coverage_complete": False,
        },
    }
    assert client.calls == [
        "clock",
        "account_numbers",
        ("select_account", "synthetic-account-a"),
        ("get_balances", "synthetic-account-a"),
        ("get_positions", "synthetic-account-a"),
        "clock",
    ]
    assert result["broker_account_id"] == "synthetic-account-a"
    assert not {"account_type", "broker_environment", "account_hash"}.intersection(result)


@pytest.mark.parametrize("positions_method", [None, "raises"])
def test_balance_only_read_does_not_require_or_call_positions(positions_method):
    client = CachedReadOnlyClient()
    if positions_method is None:
        client.get_positions = None
    else:
        def fail_if_called(_account):
            raise RuntimeError("synthetic invalid positions")

        client.get_positions = fail_if_called

    result = collect_firstrade_account_facts(
        client,
        expected_account="synthetic-account-a",
        include_positions=False,
        clock=lambda: datetime(2026, 10, 8, 12, tzinfo=timezone.utc),
    )

    assert result["balances"]["provider_equity"] == "1234.5"
    assert result["balances"]["cash_balance"] == "200.25"
    assert result["positions_status"] == "not_requested"
    assert result["positions"] is None
    assert result["coverage"]["positions_requested"] is False
    assert result["coverage"]["positions_count"] is None
    assert result["coverage"]["positions_missing_market_value"] is None
    assert result["coverage"]["positions_missing_identity_fields"] is None
    assert result["coverage"]["positions_missing_currency"] is None
    assert result["coverage"]["currency_coverage_complete"] is False
    assert ("get_positions", "synthetic-account-a") not in client.calls


def test_rejects_missing_or_non_read_only_client_before_provider_reads():
    client = CachedReadOnlyClient()
    client.session_reused = False

    with pytest.raises(AccountFactsUnavailable) as error:
        collect_firstrade_account_facts(client, expected_account="synthetic-account-a")

    assert error.value.reason_code == "readonly_client_unavailable"
    assert client.calls == []


def test_rejects_identity_mismatch_without_reading_balances_or_positions():
    client = CachedReadOnlyClient()
    client.accounts = ["synthetic-account-b"]

    with pytest.raises(AccountFactsUnavailable) as error:
        collect_firstrade_account_facts(client, expected_account="synthetic-account-a")

    assert error.value.reason_code == "account_identity_mismatch"
    assert client.calls == ["account_numbers"]
    assert "synthetic-account-a" not in str(error.value)
    assert "synthetic-account-b" not in str(error.value)


def test_rejects_provider_selector_result_that_differs_from_expected_identity():
    client = CachedReadOnlyClient()
    client.selected_account = "synthetic-account-b"

    with pytest.raises(AccountFactsUnavailable) as error:
        collect_firstrade_account_facts(client, expected_account="synthetic-account-a")

    assert error.value.reason_code == "account_identity_mismatch"
    assert client.calls == ["account_numbers", ("select_account", "synthetic-account-a")]


def test_keeps_provider_cash_and_buying_power_fields_distinct():
    client = CachedReadOnlyClient()
    client.balances = {"available_cash": "$75.00", "buying_power": "$90.00", "currency": "usd"}

    result = collect_firstrade_account_facts(
        client,
        expected_account="synthetic-account-a",
        clock=lambda: datetime(2026, 10, 8, 12, tzinfo=timezone.utc),
    )

    assert result["balances"] == {
        "provider_equity": None,
        "cash_balance": None,
        "available_cash": "75",
        "buying_power": "90",
        "currency": "USD",
    }
    assert result["coverage"]["cash_balance_available"] is False
    assert result["coverage"]["available_cash_available"] is True
    assert result["coverage"]["buying_power_available"] is True


def test_does_not_mix_margin_cash_with_cash_or_buying_power():
    client = CachedReadOnlyClient()
    client.balances = {
        "margin_cash": "$200.00",
        "margin_buying_power": "$300.00",
        "cash_balance": "$100.00",
        "available_cash": "$90.00",
        "buying_power": "$80.00",
        "currency_code": "USD",
    }

    result = collect_firstrade_account_facts(
        client,
        expected_account="synthetic-account-a",
        clock=lambda: datetime(2026, 10, 8, 12, tzinfo=timezone.utc),
    )

    assert result["balances"] == {
        "provider_equity": None,
        "cash_balance": "100",
        "available_cash": "90",
        "buying_power": "80",
        "currency": "USD",
    }


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("1,2", "provider_value_invalid"),
        ("1e999", "provider_value_invalid"),
        ("9" * 80, "provider_value_out_of_range"),
        ("0." + "1" * 29, "provider_value_out_of_range"),
    ],
)
def test_rejects_invalid_or_unbounded_provider_decimal_text(value, reason):
    client = CachedReadOnlyClient()
    client.balances = {"total_equity": value, "currency": "USD"}

    with pytest.raises(AccountFactsUnavailable) as error:
        collect_firstrade_account_facts(client, expected_account="synthetic-account-a")

    assert error.value.reason_code == reason


def test_ignores_nested_same_named_values_outside_account_level_scope():
    client = CachedReadOnlyClient()
    client.balances = {
        "total_equity": "100.00",
        "summary": {"total_equity": "101.00"},
        "currency": "USD",
    }

    result = collect_firstrade_account_facts(
        client,
        expected_account="synthetic-account-a",
        clock=lambda: datetime(2026, 10, 8, 12, tzinfo=timezone.utc),
    )

    assert result["balances"]["provider_equity"] == "100"


def test_does_not_infer_account_facts_from_nested_margin_fields():
    client = CachedReadOnlyClient()
    client.balances = {
        "margin": {
            "total_equity": "1000.00",
            "cash_balance": "100.00",
            "currency": "USD",
        }
    }

    result = collect_firstrade_account_facts(
        client,
        expected_account="synthetic-account-a",
        clock=lambda: datetime(2026, 10, 8, 12, tzinfo=timezone.utc),
    )

    assert result["balances"]["provider_equity"] is None
    assert result["balances"]["cash_balance"] is None
    assert result["balances"]["currency"] is None


def test_uses_only_exact_balance_labels_and_never_cash_substrings_as_equity():
    client = CachedReadOnlyClient()
    client.balances = {
        "total_cash_value": "100.00",
        "total_equity": "1000.00",
        "unsettled_cash": "7.00",
        "settled_cash": "8.00",
        "margin_cash": "9.00",
        "buying_power": "10.00",
        "currency": "USD",
    }

    result = collect_firstrade_account_facts(
        client,
        expected_account="synthetic-account-a",
        clock=lambda: datetime(2026, 10, 8, 12, tzinfo=timezone.utc),
    )

    assert result["balances"]["provider_equity"] == "1000"
    assert result["balances"]["cash_balance"] is None
    assert result["balances"]["buying_power"] == "10"


@pytest.mark.parametrize(
    "field",
    ["total_cash_value", "unsettled_cash", "settled_cash", "margin_cash", "buying_power", "available_cash"],
)
def test_cash_only_publishes_exact_cash_balance_field(field):
    client = CachedReadOnlyClient()
    client.balances = {field: "7.00", "currency": "USD"}

    result = collect_firstrade_account_facts(
        client,
        expected_account="synthetic-account-a",
        clock=lambda: datetime(2026, 10, 8, 12, tzinfo=timezone.utc),
    )

    assert result["balances"]["cash_balance"] is None


def test_preserves_valid_parenthesized_negative_provider_decimal():
    client = CachedReadOnlyClient()
    client.balances["total_equity"] = "($1,234.50)"

    result = collect_firstrade_account_facts(
        client,
        expected_account="synthetic-account-a",
        clock=lambda: datetime(2026, 10, 8, 12, tzinfo=timezone.utc),
    )

    assert result["balances"]["provider_equity"] == "-1234.5"


def test_missing_provider_currency_stays_unknown_in_coverage():
    client = CachedReadOnlyClient()
    client.balances.pop("currency")

    result = collect_firstrade_account_facts(
        client,
        expected_account="synthetic-account-a",
        clock=lambda: datetime(2026, 10, 8, 12, tzinfo=timezone.utc),
    )

    assert result["balances"]["currency"] is None
    assert result["coverage"]["balance_currency_available"] is False
    assert result["coverage"]["currency_coverage_complete"] is False


@pytest.mark.parametrize("currency", ["USDT", "US D", "U$D", "12A"])
def test_rejects_non_iso_currency_shape(currency):
    client = CachedReadOnlyClient()
    client.balances["currency"] = currency

    with pytest.raises(AccountFactsUnavailable) as error:
        collect_firstrade_account_facts(client, expected_account="synthetic-account-a")

    assert error.value.reason_code == "provider_currency_invalid"


@pytest.mark.parametrize("surface", ["balances", "positions"])
def test_provider_read_failure_is_redacted(surface):
    client = CachedReadOnlyClient()
    def fail(_account):
        raise RuntimeError("synthetic private provider payload")

    setattr(client, f"get_{surface}", fail)

    with pytest.raises(AccountFactsUnavailable) as error:
        collect_firstrade_account_facts(client, expected_account="synthetic-account-a")

    assert error.value.reason_code == f"{surface}_unavailable"
    assert "synthetic private provider payload" not in str(error.value)


@pytest.mark.parametrize("positions", [{}, {"items": None}, {"items": [None]}])
def test_rejects_unreadable_positions_shape_instead_of_reporting_empty_account(positions):
    client = CachedReadOnlyClient()
    client.positions = positions

    with pytest.raises(AccountFactsUnavailable) as error:
        collect_firstrade_account_facts(client, expected_account="synthetic-account-a")

    assert error.value.reason_code == "positions_unavailable"


def test_requires_timezone_aware_monotonic_observation_times():
    client = CachedReadOnlyClient()
    values = iter((datetime(2026, 10, 8), datetime(2026, 10, 7, tzinfo=timezone.utc)))

    with pytest.raises(AccountFactsUnavailable) as error:
        collect_firstrade_account_facts(
            client,
            expected_account="synthetic-account-a",
            clock=lambda: next(values),
        )

    assert error.value.reason_code == "observation_time_invalid"


@pytest.mark.parametrize(
    "field",
    ["total_value", "account_list_total_value", "total_equity"],
)
def test_accepts_firstrade_total_value_as_provider_equity(field):
    client = CachedReadOnlyClient()
    client.balances = {field: "$1,234.50", "currency": "USD"}

    result = collect_firstrade_account_facts(
        client,
        expected_account="synthetic-account-a",
        clock=lambda: datetime(2026, 10, 8, 12, tzinfo=timezone.utc),
    )

    assert result["balances"]["provider_equity"] == "1234.5"
