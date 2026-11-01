from __future__ import annotations

from datetime import date, timedelta
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

# Review pin: the date the fee schedule is expected to be verified through. The monthly
# fee-schedule check workflow moves this line together with ``verified_through``.
VERIFIED_THROUGH = date(2026, 12, 1)
DAY_AFTER_VERIFIED = VERIFIED_THROUGH + timedelta(days=1)

# Shape of the Firstrade history CSV row for the 2026-08-28 margin sellout.
# ``Date`` is the trade date (matches EXEC TIME); S/D is the T+1 settlement date.
AUG_28_IBIT_SELL = {
    "Date": "8/28/2026",
    "Transaction": "Sold",
    "Quantity": "-2",
    "Description": (
        "ISHARES BITCOIN TR ISHARES BITCOIN TRUST ETF BROKER/DEALER ORDER MARGIN SELLOUT "
        "UNSOLICITED EXEC TIME: 2026-08-28 14:50:14 S/D: 08/31/2026"),
    "Symbol": "IBIT",
    "Price": "43.7724",
    "Amount": "67.58",
}


def sell_on(trade_date: str, **overrides):
    """The Aug 28 sell re-dated to ``trade_date`` (YYYY-MM-DD), EXEC TIME kept in step."""

    return {**AUG_28_IBIT_SELL, "Date": trade_date,
            "Description": f"BROKER/DEALER ORDER EXEC TIME: {trade_date} 10:00:00", **overrides}


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
    before = decompose_sell_fees(sell_on("2026-04-03"))
    after = decompose_sell_fees(sell_on("2026-04-04"))
    assert before["sec_fee_unrounded"] == "0"
    assert before["regulatory_fee"] == "0.01"
    assert after["sec_fee_unrounded"] == "0.00180342288"
    assert after["regulatory_fee"] == "0.01"

    large = {"Quantity": "-1000", "Price": "100", "Amount": "99977.79"}
    # 100,000 x 20.60/M = 2.06; TAF 1000 x 0.000195 = 0.195; sum 2.255 -> 2.26.
    result = decompose_sell_fees(sell_on("2026-08-28", **large))
    assert result["regulatory_fee"] == "2.26"
    assert result["expected_net"] == "99977.79"
    # Rate was zero before 2026-04-04: only TAF, 0.195 -> 0.20.
    zero_sec = decompose_sell_fees(sell_on("2026-01-15", **large))
    assert zero_sec["sec_fee_unrounded"] == "0"
    assert zero_sec["regulatory_fee"] == "0.20"


def test_taf_pause_and_cap_follow_the_schedule():
    schedule = load_fee_schedule()
    taf_195 = (Decimal("0.000195"), Decimal("9.79"))
    assert schedule.taf_on(date(2025, 12, 31)) == (Decimal("0.000166"), Decimal("8.30"))
    assert schedule.taf_on(date(2026, 1, 1)) == taf_195
    assert schedule.taf_on(date(2026, 9, 30)) == taf_195
    # SR-FINRA-2026-021: TAF is $0 from 2026-10-01 to 2026-12-31.
    assert schedule.taf_on(date(2026, 10, 1)) == (Decimal(0), Decimal(0))
    assert schedule.taf_on(date(2026, 11, 1)) == (Decimal(0), Decimal(0))
    capped = decompose_sell_fees(sell_on(
        "2026-02-02", Quantity="-100000", Price="1", Amount="0"))
    assert capped["taf_unrounded"] == "9.79"


def test_taf_increase_is_deferred_to_2029_by_sr_finra_2026_020():
    # Verify with a schedule whose verified_through covers the later years.
    config = {**DEFAULT_FEE_SCHEDULE_CONFIG, "verified_through": "2031-12-31"}
    schedule = load_fee_schedule(config)
    taf_195 = (Decimal("0.000195"), Decimal("9.79"))
    assert schedule.taf_on(date(2027, 1, 1)) == taf_195
    assert schedule.taf_on(date(2027, 12, 31)) == taf_195
    assert schedule.taf_on(date(2028, 1, 1)) == taf_195
    assert schedule.taf_on(date(2028, 12, 31)) == taf_195
    assert schedule.taf_on(date(2029, 1, 1)) == (Decimal("0.000232"), Decimal("11.61"))
    assert schedule.taf_on(date(2030, 1, 1)) == (Decimal("0.000240"), Decimal("12.05"))
    assert schedule.taf_on(date(2031, 1, 1)) == (Decimal("0.000249"), Decimal("12.50"))


def test_dates_after_verified_through_are_unsupported():
    schedule = load_fee_schedule()
    pin = VERIFIED_THROUGH.isoformat()
    assert schedule.verified_through == VERIFIED_THROUGH
    assert schedule.sec_rate_on(VERIFIED_THROUGH) == Decimal("20.60")
    for lookup in (schedule.sec_rate_on, schedule.taf_on, schedule.broker_assisted_fee_on):
        with pytest.raises(FeeScheduleError, match=f"not_verified_after_{pin}"):
            lookup(DAY_AFTER_VERIFIED)
    # SEC FY2027 Section 31 rate is not published: a later sale must not borrow FY2026's.
    late = decompose_sell_fees(sell_on(DAY_AFTER_VERIFIED.isoformat()))
    assert late["status"] == "unsupported"
    assert f"not_verified_after_{pin}" in late["reason"]
    assert decompose_sell_fees(sell_on(pin))["status"] != "unsupported"


def test_verified_through_is_required_and_must_be_a_date():
    config = {k: v for k, v in DEFAULT_FEE_SCHEDULE_CONFIG.items() if k != "verified_through"}
    with pytest.raises(FeeScheduleError, match="verified_through_missing"):
        load_fee_schedule(config)
    with pytest.raises(FeeScheduleError, match="verified_through_invalid"):
        load_fee_schedule({**config, "verified_through": "tomorrow"})


def test_non_broker_assisted_or_non_sell_rows_are_not_applicable():
    online = decompose_sell_fees({**AUG_28_IBIT_SELL, "Description": "SOLD IBIT ONLINE"})
    buy = decompose_sell_fees({**AUG_28_IBIT_SELL, "Transaction": "Bought"})
    assert online == {"status": "not_applicable", "reason": "not_broker_assisted_order"}
    assert buy == {"status": "not_applicable", "reason": "not_a_sell"}


def test_dates_outside_the_schedule_and_bad_input_fail_closed():
    early = decompose_sell_fees(sell_on("2020-01-02"))
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
        "quantity_check": "not_performed",
    }


def test_valuation_tolerance_is_floor_or_tenth_of_a_percent():
    small = valuation_basis_difference(
        balance_long_stock_value="10", positions_market_values=["10.10"])
    assert small["status"] == "valuation_basis_difference" and small["tolerance"] == "0.1"
    just_over = valuation_basis_difference(
        balance_long_stock_value="10", positions_market_values=["10.11"])
    assert just_over["status"] == "unexplained"
    big = valuation_basis_difference(
        balance_long_stock_value="10000", positions_market_values=["10010"])
    assert big["status"] == "valuation_basis_difference" and big["tolerance"] == "10.01"
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


def test_csv_date_is_trade_date_and_settlement_date_never_selects_the_rate():
    result = decompose_sell_fees(AUG_28_IBIT_SELL)
    assert result["trade_date"] == "2026-08-28"
    assert result["settlement_date"] == "2026-08-31"

    # Trade on the last day of a rate segment, settlement after the boundary:
    # the rate follows the trade date (SEC FY2026 rate starts 2026-04-04).
    last_zero_day = sell_on(
        "2026-04-03", Description="BROKER/DEALER ORDER EXEC TIME: 2026-04-03 10:00:00 S/D: 04/06/2026")
    assert decompose_sell_fees(last_zero_day)["sec_fee_unrounded"] == "0"
    # And the TAF pause: a 2026-09-30 trade settling 2026-10-01 still pays TAF.
    pause_edge = sell_on(
        "2026-09-30", Quantity="-100000", Price="1", Amount="0",
        Description="BROKER/DEALER ORDER EXEC TIME: 2026-09-30 10:00:00 S/D: 10/01/2026")
    assert decompose_sell_fees(pause_edge)["taf_unrounded"] == "9.79"


def test_date_column_that_is_not_the_trade_date_fails_closed():
    exported_settlement = {**AUG_28_IBIT_SELL, "Date": "8/31/2026"}
    wrong = decompose_sell_fees(exported_settlement)
    assert wrong == {"status": "unsupported", "reason": "date_column_not_trade_date"}
    same_day = {**AUG_28_IBIT_SELL, "Description": AUG_28_IBIT_SELL["Description"].replace(
        "S/D: 08/31/2026", "S/D: 08/28/2026")}
    assert decompose_sell_fees(same_day)["reason"] == "date_column_not_trade_date"
    # Without EXEC TIME or S/D the Date column is used as the trade date.
    plain = {**AUG_28_IBIT_SELL, "Description": "BROKER/DEALER ORDER"}
    assert decompose_sell_fees(plain)["trade_date"] == "2026-08-28"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"Quantity": "0"}, "quantity_not_positive"),
        ({"Quantity": "-0"}, "quantity_not_positive"),
        ({"Price": "0"}, "price_not_positive"),
        ({"Price": "-1"}, "price_not_positive"),
        ({"Price": "0.00"}, "price_not_positive"),
    ],
)
def test_non_positive_quantity_or_price_is_unsupported(overrides, reason):
    result = decompose_sell_fees({**AUG_28_IBIT_SELL, **overrides})
    assert result == {"status": "unsupported", "reason": reason}


def test_smallest_positive_quantity_and_price_are_accepted():
    tiny = decompose_sell_fees({
        **AUG_28_IBIT_SELL, "Quantity": "-0.00001", "Price": "0.0001", "Amount": "-19.96"})
    assert tiny["status"] == "explained"
    assert tiny["gross_proceeds"] == "0.000000001"
    assert tiny["regulatory_fee"] == "0.01"
    one_share = decompose_sell_fees({**AUG_28_IBIT_SELL, "Quantity": "1", "Price": "0.01",
                                     "Amount": "-19.96"})
    assert one_share["status"] == "explained"


def test_valuation_tolerance_has_an_absolute_cap():
    # 0.1% of 100,000 would be 100; the cap holds it at 25.
    inside = valuation_basis_difference(
        balance_long_stock_value="100000", positions_market_values=["100025"])
    assert inside["status"] == "valuation_basis_difference" and inside["tolerance"] == "25"
    outside = valuation_basis_difference(
        balance_long_stock_value="100000", positions_market_values=["100025.01"])
    assert outside["status"] == "unexplained"
    assert outside["reason"] == "difference_exceeds_tolerance"
    assert outside["tolerance"] == "25"
    custom = valuation_basis_difference(
        balance_long_stock_value="100000", positions_market_values=["100050"],
        tolerance_cap=Decimal("50"))
    assert custom["status"] == "valuation_basis_difference"


def test_empty_positions_only_match_a_zero_balance_value():
    assert valuation_basis_difference(
        balance_long_stock_value="0", positions_market_values=[])["status"] == "match"
    # A small non-zero balance with no positions must not pass as a basis difference.
    stale = valuation_basis_difference(balance_long_stock_value="0.05", positions_market_values=[])
    assert stale["status"] == "unexplained"
    assert stale["reason"] == "positions_empty_balance_nonzero"
    report = valuation_basis_report({"balances": {"long_stock_value": "130.56"}, "positions": []})
    assert report["status"] == "unexplained"
    zero = valuation_basis_report({"balances": {"long_stock_value": "0"}, "positions": []})
    assert zero["status"] == "match"


def test_negative_valuation_inputs_are_unavailable():
    assert valuation_basis_difference(
        balance_long_stock_value="-1", positions_market_values=["1"])["reason"] == "valuation_input_negative"
    assert valuation_basis_difference(
        balance_long_stock_value="1", positions_market_values=["-1"])["status"] == "unavailable"


def test_position_quantity_cross_check_blocks_a_basis_explanation():
    kwargs = dict(balance_long_stock_value="130.56", positions_market_values=["130.6232"])
    matched = valuation_basis_difference(
        **kwargs, position_quantities={"ibit": "2.98909"}, expected_quantities={"IBIT": "2.98909"})
    assert matched["status"] == "valuation_basis_difference"
    assert matched["quantity_check"] == "matched"
    changed = valuation_basis_difference(
        **kwargs, position_quantities={"IBIT": "2.98909"}, expected_quantities={"IBIT": "3"})
    assert changed["status"] == "unexplained" and changed["reason"] == "quantity_mismatch"
    extra = valuation_basis_difference(
        **kwargs, position_quantities={"IBIT": "2.98909", "SPY": "1"},
        expected_quantities={"IBIT": "2.98909"})
    assert extra["reason"] == "quantity_mismatch"
    # Quantities not supplied: the result says so instead of implying a check.
    assert valuation_basis_difference(**kwargs)["quantity_check"] == "not_performed"
    bad = valuation_basis_difference(
        **kwargs, position_quantities={"IBIT": "0"}, expected_quantities={"IBIT": "0"})
    assert bad["status"] == "unavailable"


def test_valuation_report_cross_checks_collector_quantities():
    observation = {
        "balances": {"long_stock_value": "130.56"},
        "positions": [{"symbol": "IBIT", "quantity": "2.98909", "market_value": "130.6232"}],
    }
    ok = valuation_basis_report(observation, expected_quantities={"IBIT": "2.98909"})
    assert ok["status"] == "valuation_basis_difference" and ok["quantity_check"] == "matched"
    assert valuation_basis_report(
        observation, expected_quantities={"IBIT": "2"})["reason"] == "quantity_mismatch"
    no_qty = {"balances": observation["balances"],
              "positions": [{"symbol": "IBIT", "quantity": None, "market_value": "130.6232"}]}
    assert valuation_basis_report(
        no_qty, expected_quantities={"IBIT": "2.98909"})["reason"] == "position_quantity_missing"
