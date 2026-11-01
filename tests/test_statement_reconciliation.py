from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from application.firstrade_fee_schedule import (
    DEFAULT_FEE_SCHEDULE_CONFIG,
    FeeScheduleError,
    load_fee_schedule,
)
from application.statement_reconciliation import (
    decompose_sell_fees,
    valuation_basis_difference,
    valuation_basis_report,
)

# Shape of the Firstrade history CSV row for the 2026-08-28 margin sellout.
AUG_28_IBIT_SELL = {
    "Date": "8/28/2026",
    "Transaction": "Sold",
    "Quantity": "-2",
    "Description": "BROKER/DEALER ORDER MARGIN SELLOUT UNSOLICITED EXEC TIME: 2026-08-28 14:50:14",
    "Symbol": "IBIT",
    "Price": "43.7724",
    "Amount": "67.58",
}


def test_aug_28_ibit_margin_sellout_is_explained_by_two_fees():
    result = decompose_sell_fees(AUG_28_IBIT_SELL)

    assert result["status"] == "explained"
    assert result["gross_proceeds"] == "87.5448"
    assert result["broker_assisted_fee"] == "19.95"
    assert result["regulatory_fee"] == "0.01"
    assert result["expected_net"] == "67.58"
    assert result["reported_net"] == "67.58"
    assert result["residual"] == "0.00"
    # The statement gap that used to look like an unexplained $19.96.
    assert result["gross_minus_reported_net"] == "19.9648"


def test_residual_within_one_cent_is_explained_and_beyond_is_not():
    off_by_cent = decompose_sell_fees({**AUG_28_IBIT_SELL, "Amount": "67.59"})
    off_by_two = decompose_sell_fees({**AUG_28_IBIT_SELL, "Amount": "67.60"})

    assert off_by_cent["status"] == "explained" and off_by_cent["residual"] == "0.01"
    assert off_by_two["status"] == "unexplained"
    assert off_by_two["reason"] == "residual_exceeds_tolerance"


def test_sec_rate_is_segmented_by_effective_date():
    # 2026-04-03 is the last day of the $0.00/M segment: no SEC fee at all, and
    # TAF on 2 shares (0.00039) still rounds up to the one-cent minimum.
    before = decompose_sell_fees({**AUG_28_IBIT_SELL, "Date": "2026-04-03"})
    after = decompose_sell_fees({**AUG_28_IBIT_SELL, "Date": "2026-04-04"})
    assert before["sec_fee_unrounded"] == "0"
    assert before["regulatory_fee"] == "0.01"
    assert after["sec_fee_unrounded"] == "0.00180342288"
    assert after["regulatory_fee"] == "0.01"

    large = {**AUG_28_IBIT_SELL, "Quantity": "-1000", "Price": "100", "Amount": "99977.79"}
    # 100,000 x 20.60/M = 2.06; TAF 1000 x 0.000195 = 0.195; sum 2.255 -> 2.26.
    result = decompose_sell_fees({**large, "Date": "2026-08-28"})
    assert result["regulatory_fee"] == "2.26"
    assert result["expected_net"] == "99977.79"
    # Rate was zero before 2026-04-04: only TAF, 0.195 -> 0.20.
    zero_sec = decompose_sell_fees({**large, "Date": "2026-01-15"})
    assert zero_sec["sec_fee_unrounded"] == "0"
    assert zero_sec["regulatory_fee"] == "0.20"


def test_taf_pause_and_cap_follow_the_schedule():
    schedule = load_fee_schedule()
    assert schedule.taf_on(date(2026, 9, 30)) == (Decimal("0.000195"), Decimal("9.79"))
    assert schedule.taf_on(date(2026, 10, 1))[0] == 0
    assert schedule.taf_on(date(2027, 1, 1))[0] > 0
    capped = decompose_sell_fees({
        **AUG_28_IBIT_SELL, "Date": "2026-02-02", "Quantity": "-100000", "Price": "1",
        "Amount": "0",
    })
    assert capped["taf_unrounded"] == "9.79"


def test_non_broker_assisted_or_non_sell_rows_are_not_applicable():
    online = decompose_sell_fees({**AUG_28_IBIT_SELL, "Description": "SOLD IBIT ONLINE"})
    buy = decompose_sell_fees({**AUG_28_IBIT_SELL, "Transaction": "Bought"})
    assert online == {"status": "not_applicable", "reason": "not_broker_assisted_order"}
    assert buy == {"status": "not_applicable", "reason": "not_a_sell"}


def test_dates_outside_the_schedule_and_bad_input_fail_closed():
    early = decompose_sell_fees({**AUG_28_IBIT_SELL, "Date": "2020-01-02"})
    assert early["status"] == "unsupported"
    assert "not_defined_for_2020-01-02" in early["reason"]
    assert decompose_sell_fees({**AUG_28_IBIT_SELL, "Amount": "n/a"})["status"] == "unsupported"
    assert decompose_sell_fees({**AUG_28_IBIT_SELL, "Date": "soon"})["status"] == "unsupported"


def test_schedule_loader_rejects_malformed_config():
    bad = {**DEFAULT_FEE_SCHEDULE_CONFIG, "sec_rate_per_million": [
        {"effective_from": "2026-04-04", "rate": "20.60"},
        {"effective_from": "2025-05-14", "rate": "0.00"},
    ]}
    with pytest.raises(FeeScheduleError, match="not_strictly_ordered"):
        load_fee_schedule(bad)
    with pytest.raises(FeeScheduleError, match="schema_version_unsupported"):
        load_fee_schedule({**DEFAULT_FEE_SCHEDULE_CONFIG, "schema_version": "x"})
    with pytest.raises(FeeScheduleError, match="rate_invalid"):
        load_fee_schedule({**DEFAULT_FEE_SCHEDULE_CONFIG, "sec_rate_per_million": [
            {"effective_from": "2026-04-04", "rate": "-1"}]})


def test_schedule_is_overridable_from_config():
    custom = {**DEFAULT_FEE_SCHEDULE_CONFIG,
              "broker_assisted_fee": [{"effective_from": "2000-01-01", "amount": "25.00"}]}
    result = decompose_sell_fees(AUG_28_IBIT_SELL, schedule=load_fee_schedule(custom))
    assert result["broker_assisted_fee"] == "25.00"
    assert result["status"] == "unexplained"


def test_130_56_vs_130_6232_is_a_valuation_basis_difference_not_unexplained():
    result = valuation_basis_difference(
        balance_long_stock_value="130.56", positions_market_values=["130.6232"])

    assert result == {
        "status": "valuation_basis_difference",
        "balance_long_stock_value": "130.56",
        "positions_market_value": "130.6232",
        "difference_positions_minus_balance": "0.0632",
        "tolerance": "0.1306232",
    }


def test_valuation_tolerance_is_floor_or_tenth_of_a_percent():
    small = valuation_basis_difference(
        balance_long_stock_value="10", positions_market_values=["10.10"])
    assert small["status"] == "valuation_basis_difference" and small["tolerance"] == "0.1"
    just_over = valuation_basis_difference(
        balance_long_stock_value="10", positions_market_values=["10.11"])
    assert just_over["status"] == "unexplained"
    big = valuation_basis_difference(
        balance_long_stock_value="100000", positions_market_values=["100099"])
    assert big["status"] == "valuation_basis_difference"
    assert valuation_basis_difference(
        balance_long_stock_value="5", positions_market_values=["5"])["status"] == "match"


def test_valuation_report_uses_collector_observation_and_fails_closed():
    observation = {
        "balances": {"long_stock_value": "130.56"},
        "positions": [{"symbol": "IBIT", "market_value": "130.6232"}],
    }
    assert valuation_basis_report(observation)["status"] == "valuation_basis_difference"
    missing = {"balances": {"long_stock_value": None}, "positions": []}
    assert valuation_basis_report(missing) == {
        "status": "unavailable", "reason": "balance_long_stock_value_missing"}
    no_mv = {"balances": {"long_stock_value": "1"}, "positions": [{"symbol": "X", "market_value": None}]}
    assert valuation_basis_report(no_mv)["reason"] == "position_market_value_missing"
    assert valuation_basis_report({})["reason"] == "observation_incomplete"
