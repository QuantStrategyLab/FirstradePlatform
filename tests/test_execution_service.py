from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from application.execution_service import (
    _apply_notional_cash_buffer,
    execute_value_target_plan,
    substitute_small_safe_haven_targets_with_cash,
)
from quant_platform_kit.common.models import ExecutionReport, QuoteSnapshot


@dataclass
class FakeMarketDataPort:
    prices: dict[str, float]

    def get_quote(self, symbol: str) -> QuoteSnapshot:
        normalized = symbol.upper()
        return QuoteSnapshot(
            symbol=normalized,
            as_of=datetime.now(timezone.utc),
            last_price=self.prices[normalized],
        )


class FakeExecutionPort:
    def __init__(self) -> None:
        self.orders = []

    def submit_order(self, order_intent) -> ExecutionReport:
        self.orders.append(order_intent)
        return ExecutionReport(
            symbol=order_intent.symbol,
            side=order_intent.side,
            quantity=order_intent.quantity,
            status="previewed",
            broker_order_id=f"OID-{len(self.orders)}",
            raw_payload={
                "limit_price": order_intent.limit_price,
                "max_notional_usd": order_intent.metadata.get("max_notional_usd"),
            },
        )


class SubmittedExecutionPort(FakeExecutionPort):
    def submit_order(self, order_intent) -> ExecutionReport:
        self.orders.append(order_intent)
        return ExecutionReport(
            symbol=order_intent.symbol,
            side=order_intent.side,
            quantity=order_intent.quantity,
            status="submitted",
            broker_order_id=f"OID-{len(self.orders)}",
            raw_payload={},
        )


def test_execute_value_target_plan_sells_before_buys_and_caps_order_notional():
    execution_port = FakeExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {"targets": {"AAA": 50.0, "BBB": 0.0}},
            "portfolio": {
                "market_values": {"AAA": 0.0, "BBB": 60.0},
                "sellable_quantities": {"BBB": 6.0},
                "liquid_cash": 100.0,
            },
            "execution": {"current_min_trade": 5.0, "investable_cash": 100.0},
        },
        market_data_port=FakeMarketDataPort({"AAA": 10.0, "BBB": 10.0}),
        execution_port=execution_port,
        dry_run_only=True,
        max_order_notional_usd=25.0,
    )

    assert result.action_done is True
    assert [(order.side, order.symbol, order.quantity) for order in execution_port.orders] == [
        ("sell", "BBB", 2.0),
        ("buy", "AAA", 2.0),
    ]
    assert execution_port.orders[0].limit_price == 9.95
    assert execution_port.orders[1].limit_price == 10.05
    assert all(order.metadata["max_notional_usd"] == 25.0 for order in execution_port.orders)


def test_execute_value_target_plan_marks_live_submissions_pending_reconciliation():
    execution_port = SubmittedExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {"targets": {"AAA": 20.0}},
            "portfolio": {
                "market_values": {"AAA": 0.0},
                "sellable_quantities": {"AAA": 0.0},
                "liquid_cash": 100.0,
            },
            "execution": {"current_min_trade": 5.0, "investable_cash": 100.0},
        },
        market_data_port=FakeMarketDataPort({"AAA": 10.0}),
        execution_port=execution_port,
        dry_run_only=False,
    )

    assert result.action_done is False
    assert result.broker_submission_done is True
    assert result.pending_reconciliation is True
    assert len(result.submitted_orders) == 1


def test_execute_value_target_plan_uses_sellable_quantity_when_market_value_is_stale_below_quote():
    execution_port = FakeExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {"targets": {"SOXL": 0.0}},
            "portfolio": {
                "market_values": {"SOXL": 524.10},
                "sellable_quantities": {"SOXL": 3.0},
                "liquid_cash": 0.0,
            },
            "execution": {"current_min_trade": 5.0, "investable_cash": 0.0},
        },
        market_data_port=FakeMarketDataPort({"SOXL": 175.42}),
        execution_port=execution_port,
        dry_run_only=True,
        max_order_notional_usd=1000.0,
    )

    assert result.action_done is True
    assert [(order.side, order.symbol, order.quantity) for order in execution_port.orders] == [
        ("sell", "SOXL", 3.0),
    ]


def test_execute_value_target_plan_keeps_existing_whole_share_when_positive_target_is_unbuyable():
    execution_port = FakeExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {
                "targets": {"TQQQ": 60.94, "QQQM": 320.0},
                "risk_symbols": ("TQQQ", "QQQM"),
            },
            "portfolio": {
                "market_values": {"TQQQ": 541.31, "QQQM": 0.0},
                "quantities": {"TQQQ": 7.0, "QQQM": 0.0},
                "sellable_quantities": {"TQQQ": 7.0, "QQQM": 0.0},
                "liquid_cash": 539.70,
            },
            "execution": {"current_min_trade": 10.0, "investable_cash": 539.70},
        },
        market_data_port=FakeMarketDataPort({"TQQQ": 77.33, "QQQM": 297.19}),
        execution_port=execution_port,
        dry_run_only=True,
    )

    assert [(order.side, order.symbol, order.quantity) for order in execution_port.orders if order.side == "sell"] == [
        ("sell", "TQQQ", 6.0),
    ]


def test_execute_value_target_plan_tops_up_existing_whole_share_when_target_rounds_up():
    execution_port = FakeExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {
                "targets": {"SOXL": 0.0, "SOXX": 260.0},
                "risk_symbols": ("SOXL", "SOXX"),
            },
            "portfolio": {
                "market_values": {"SOXL": 120.0, "SOXX": 200.0},
                "quantities": {"SOXL": 3.0, "SOXX": 2.0},
                "sellable_quantities": {"SOXL": 3.0, "SOXX": 2.0},
                "liquid_cash": 10.0,
            },
            "execution": {"current_min_trade": 10.0, "investable_cash": 10.0},
        },
        market_data_port=FakeMarketDataPort({"SOXL": 40.0, "SOXX": 100.0}),
        execution_port=execution_port,
        dry_run_only=True,
        limit_buy_premium=1.0,
        fetch_order_status=lambda broker_order_id: {
            "status": "Filled" if broker_order_id else "",
            "executed_qty": 3.0,
            "executed_price": 40.0,
        },
    )

    assert result.action_done is True
    assert [(order.side, order.symbol, order.quantity) for order in execution_port.orders] == [
        ("sell", "SOXL", 3.0),
        ("buy", "SOXX", 1.0),
    ]


def test_execute_value_target_plan_defers_buy_until_sell_release_is_confirmed():
    execution_port = FakeExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {
                "targets": {"SOXL": 0.0, "SOXX": 260.0},
                "risk_symbols": ("SOXL", "SOXX"),
            },
            "portfolio": {
                "market_values": {"SOXL": 120.0, "SOXX": 200.0},
                "quantities": {"SOXL": 3.0, "SOXX": 2.0},
                "sellable_quantities": {"SOXL": 3.0, "SOXX": 2.0},
                "liquid_cash": 10.0,
            },
            "execution": {"current_min_trade": 10.0, "investable_cash": 10.0},
        },
        market_data_port=FakeMarketDataPort({"SOXL": 40.0, "SOXX": 100.0}),
        execution_port=execution_port,
        dry_run_only=True,
        limit_buy_premium=1.0,
    )

    assert result.action_done is True
    assert [(order.side, order.symbol, order.quantity) for order in execution_port.orders] == [
        ("sell", "SOXL", 3.0),
    ]
    assert result.skipped_orders == (
        {
            "symbol": "SOXX",
            "reason": "pending_sell_release",
            "pending_sell_symbols": ["SOXL"],
        },
    )


def test_execute_value_target_plan_skips_when_cap_cannot_buy_one_share():
    execution_port = FakeExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {"targets": {"SPY": 500.0}},
            "portfolio": {
                "market_values": {"SPY": 0.0},
                "sellable_quantities": {},
                "liquid_cash": 500.0,
            },
            "execution": {"current_min_trade": 1.0, "investable_cash": 500.0},
        },
        market_data_port=FakeMarketDataPort({"SPY": 100.0}),
        execution_port=execution_port,
        dry_run_only=True,
        max_order_notional_usd=25.0,
    )

    assert result.action_done is False
    assert execution_port.orders == []
    assert result.skipped_orders == (
        {"symbol": "SPY", "reason": "buy_quantity_zero", "max_order_notional_usd": 25.0},
    )


def test_execute_value_target_plan_has_no_default_order_notional_cap():
    execution_port = FakeExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {"targets": {"SPY": 500.0}},
            "portfolio": {
                "market_values": {"SPY": 0.0},
                "sellable_quantities": {},
                "liquid_cash": 500.0,
            },
            "execution": {"current_min_trade": 1.0, "investable_cash": 500.0},
        },
        market_data_port=FakeMarketDataPort({"SPY": 100.0}),
        execution_port=execution_port,
        dry_run_only=True,
        limit_buy_premium=1.0,
    )

    assert result.action_done is True
    assert [(order.side, order.symbol, order.quantity) for order in execution_port.orders] == [
        ("buy", "SPY", 5.0),
    ]
    assert execution_port.orders[0].metadata == {}


def test_execute_value_target_plan_reports_insufficient_cash_for_whole_share():
    execution_port = FakeExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {"targets": {"SPY": 500.0}},
            "portfolio": {
                "market_values": {"SPY": 0.0},
                "sellable_quantities": {},
                "liquid_cash": 50.0,
            },
            "execution": {"current_min_trade": 1.0, "investable_cash": 50.0},
        },
        market_data_port=FakeMarketDataPort({"SPY": 100.0}),
        execution_port=execution_port,
        dry_run_only=True,
        limit_buy_premium=1.0,
    )

    assert result.action_done is False
    assert execution_port.orders == []
    assert result.skipped_orders == (
        {
            "symbol": "SPY",
            "reason": "insufficient_cash_for_whole_share",
            "price": 100.0,
            "investable_cash": 50.0,
            "required_cash_for_one_share": 100.0,
        },
    )


def test_execute_value_target_plan_leaves_small_safe_haven_target_as_cash():
    execution_port = FakeExecutionPort()
    plan = {
        "allocation": {
            "targets": {"AAA": 1500.0, "BOXX": 750.0},
            "safe_haven_symbols": ("BOXX",),
        },
        "portfolio": {
            "market_values": {"AAA": 0.0, "BOXX": 0.0},
            "sellable_quantities": {},
            "liquid_cash": 2500.0,
            "cash_sweep_symbol": "BOXX",
        },
        "execution": {"current_min_trade": 1.0, "investable_cash": 2500.0},
    }

    result = execute_value_target_plan(
        plan=plan,
        market_data_port=FakeMarketDataPort({"AAA": 100.0, "BOXX": 100.0}),
        execution_port=execution_port,
        dry_run_only=True,
        limit_buy_premium=1.0,
        max_order_notional_usd=2500.0,
        safe_haven_cash_substitute_threshold_usd=1000.0,
    )

    assert result.action_done is True
    assert [(order.side, order.symbol, order.quantity) for order in execution_port.orders] == [
        ("buy", "AAA", 15.0),
    ]
    adjusted_plan = substitute_small_safe_haven_targets_with_cash(
        plan,
        threshold_usd=1000.0,
    )
    assert adjusted_plan["allocation"]["targets"]["BOXX"] == 0.0


def test_execute_value_target_plan_projects_unbuyable_value_target_to_zero():
    execution_port = FakeExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {
                "strategy_symbols": ("SOXL", "SOXX", "BOXX"),
                "risk_symbols": ("SOXL", "SOXX"),
                "safe_haven_symbols": ("BOXX",),
                "targets": {"SOXL": 541.58, "SOXX": 154.74, "BOXX": 77.37},
            },
            "portfolio": {
                "market_values": {"SOXL": 0.0, "SOXX": 536.88, "BOXX": 0.0},
                "sellable_quantities": {"SOXX": 1.0},
                "liquid_cash": 236.81,
                "cash_sweep_symbol": "BOXX",
            },
            "execution": {"current_min_trade": 7.74, "investable_cash": 213.60},
        },
        market_data_port=FakeMarketDataPort({"SOXL": 191.15, "SOXX": 536.88, "BOXX": 100.0}),
        execution_port=execution_port,
        dry_run_only=True,
        max_order_notional_usd=1000.0,
        safe_haven_cash_substitute_threshold_usd=1000.0,
        fetch_order_status=lambda broker_order_id: {
            "status": "Filled" if broker_order_id else "",
            "executed_qty": 1.0,
            "executed_price": 536.88,
        },
    )

    assert result.action_done is True
    assert [(order.side, order.symbol, order.quantity) for order in execution_port.orders] == [
        ("sell", "SOXX", 1.0),
        ("buy", "SOXL", 2.0),
    ]


def test_execute_value_target_plan_retains_near_one_share_soxx_delever_target():
    execution_port = FakeExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {
                "strategy_symbols": ("SOXL", "SOXX", "BOXX"),
                "risk_symbols": ("SOXL", "SOXX"),
                "safe_haven_symbols": ("BOXX",),
                "targets": {"SOXL": 357.21, "SOXX": 561.33, "BOXX": 102.06},
            },
            "portfolio": {
                "market_values": {"SOXL": 0.0, "SOXX": 605.17, "BOXX": 0.0},
                "quantities": {"SOXL": 0.0, "SOXX": 1.0, "BOXX": 0.0},
                "sellable_quantities": {"SOXX": 1.0},
                "liquid_cash": 519.54,
                "cash_sweep_symbol": "BOXX",
            },
            "execution": {"current_min_trade": 11.71, "investable_cash": 369.54},
        },
        market_data_port=FakeMarketDataPort({"SOXL": 232.99, "SOXX": 605.17, "BOXX": 117.06}),
        execution_port=execution_port,
        dry_run_only=True,
        max_order_notional_usd=1000.0,
        safe_haven_cash_substitute_threshold_usd=1000.0,
    )

    assert result.action_done is True
    assert [(order.side, order.symbol, order.quantity) for order in execution_port.orders] == [
        ("buy", "SOXL", 1.0),
    ]


def test_execute_value_target_plan_bootstraps_close_to_one_share_core_target():
    execution_port = FakeExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {
                "strategy_symbols": ("SOXL", "SOXX", "BOXX"),
                "risk_symbols": ("SOXL", "SOXX"),
                "safe_haven_symbols": ("BOXX",),
                "targets": {"SOXL": 218.19, "SOXX": 342.86, "BOXX": 62.34},
            },
            "portfolio": {
                "market_values": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 0.0},
                "quantities": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 0.0},
                "sellable_quantities": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 0.0},
                "liquid_cash": 623.39,
                "cash_sweep_symbol": "BOXX",
            },
            "execution": {"current_min_trade": 6.23, "investable_cash": 473.39},
        },
        market_data_port=FakeMarketDataPort({"SOXL": 229.73, "SOXX": 603.0, "BOXX": 100.0}),
        execution_port=execution_port,
        dry_run_only=True,
        limit_buy_premium=1.005,
        limit_buy_premium_by_symbol={"SOXL": 1.015},
        max_order_notional_usd=1000.0,
        safe_haven_cash_substitute_threshold_usd=1000.0,
    )

    assert result.action_done is True
    assert [(order.side, order.symbol, order.quantity, order.limit_price) for order in execution_port.orders] == [
        ("buy", "SOXL", 1.0, 233.18),
    ]
    assert result.execution_notes[0] == (
        {
            "symbol": "SOXX",
            "target_value": 342.86,
            "price": 603.0,
            "cash_symbols": (),
        }
    )
    drift_notes = [note for note in result.execution_notes if note.get("kind") == "small_account_allocation_drift"]
    assert drift_notes[0]["symbol"] == "SOXX"
    assert drift_notes[1]["symbol"] == "SOXL"


def test_execute_value_target_plan_uses_symbol_specific_limit_buy_premium_for_budget():
    execution_port = FakeExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {
                "strategy_symbols": ("SOXL",),
                "risk_symbols": ("SOXL",),
                "safe_haven_symbols": (),
                "targets": {"SOXL": 1000.0},
            },
            "portfolio": {
                "market_values": {"SOXL": 0.0},
                "quantities": {"SOXL": 0.0},
                "sellable_quantities": {"SOXL": 0.0},
                "liquid_cash": 1000.0,
                "cash_sweep_symbol": "",
            },
            "execution": {"current_min_trade": 10.0, "investable_cash": 1000.0},
        },
        market_data_port=FakeMarketDataPort({"SOXL": 100.0}),
        execution_port=execution_port,
        dry_run_only=True,
        limit_buy_premium=1.005,
        limit_buy_premium_by_symbol={"SOXL": 1.015},
        max_order_notional_usd=1000.0,
        safe_haven_cash_substitute_threshold_usd=1000.0,
    )

    assert result.action_done is True
    assert [(order.side, order.symbol, order.quantity, order.limit_price) for order in execution_port.orders] == [
        ("buy", "SOXL", 9.0, 101.5),
    ]


def test_execute_value_target_plan_keeps_safe_haven_cash_when_only_risk_target_is_unbuyable():
    execution_port = FakeExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {
                "strategy_symbols": ("SOXL", "SOXX", "BOXX"),
                "risk_symbols": ("SOXL", "SOXX"),
                "safe_haven_symbols": ("BOXX",),
                "targets": {"SOXL": 0.0, "SOXX": 194.10, "BOXX": 1099.90},
            },
            "portfolio": {
                "market_values": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 0.0},
                "sellable_quantities": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 0.0},
                "liquid_cash": 1294.00,
                "cash_sweep_symbol": "BOXX",
            },
            "execution": {"current_min_trade": 100.0, "investable_cash": 1255.18},
        },
        market_data_port=FakeMarketDataPort({"SOXL": 175.0, "SOXX": 525.0, "BOXX": 116.83}),
        execution_port=execution_port,
        dry_run_only=True,
        max_order_notional_usd=2000.0,
        safe_haven_cash_substitute_threshold_usd=1000.0,
    )

    assert result.action_done is False
    assert execution_port.orders == []
    assert result.execution_notes[0] == (
        {
            "symbol": "SOXX",
            "target_value": 194.10,
            "price": 525.0,
            "cash_symbols": ("BOXX",),
        }
    )
    drift_notes = [note for note in result.execution_notes if note.get("kind") == "small_account_allocation_drift"]
    assert [note["symbol"] for note in drift_notes] == ["SOXX"]


def test_execute_value_target_plan_uses_cash_sweep_symbol_for_small_safe_haven_cash():
    execution_port = FakeExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {
                "strategy_symbols": ("SOXX", "BOXX"),
                "risk_symbols": ("SOXX",),
                "targets": {"SOXX": 194.10, "BOXX": 1099.90},
            },
            "portfolio": {
                "market_values": {"SOXX": 0.0, "BOXX": 0.0},
                "sellable_quantities": {"SOXX": 0.0, "BOXX": 0.0},
                "liquid_cash": 1294.00,
                "cash_sweep_symbol": "BOXX",
            },
            "execution": {"current_min_trade": 100.0, "investable_cash": 1255.18},
        },
        market_data_port=FakeMarketDataPort({"SOXX": 525.0, "BOXX": 116.83}),
        execution_port=execution_port,
        dry_run_only=True,
        max_order_notional_usd=2000.0,
        safe_haven_cash_substitute_threshold_usd=1000.0,
    )

    assert result.action_done is False
    assert execution_port.orders == []


def test_execute_value_target_plan_keeps_safe_haven_when_mixed_case_risk_target_remains_buyable():
    execution_port = FakeExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {
                "strategy_symbols": ("SOXL", "SOXX", "BOXX"),
                "risk_symbols": ("soxl", "soxx"),
                "safe_haven_symbols": ("BOXX",),
                "targets": {"SOXL": 500.0, "SOXX": 194.10, "BOXX": 1000.0},
            },
            "portfolio": {
                "market_values": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 0.0},
                "sellable_quantities": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 0.0},
                "liquid_cash": 2000.0,
                "cash_sweep_symbol": "BOXX",
            },
            "execution": {"current_min_trade": 100.0, "investable_cash": 2000.0},
        },
        market_data_port=FakeMarketDataPort({"SOXL": 100.0, "SOXX": 525.0, "BOXX": 100.0}),
        execution_port=execution_port,
        dry_run_only=True,
        limit_buy_premium=1.0,
        max_order_notional_usd=2000.0,
        safe_haven_cash_substitute_threshold_usd=1000.0,
    )

    assert result.action_done is True
    assert [(order.side, order.symbol, order.quantity) for order in execution_port.orders] == [
        ("buy", "BOXX", 10.0),
        ("buy", "SOXL", 5.0),
    ]


def test_execute_value_target_plan_uses_notional_buy_when_enabled():
    execution_port = FakeExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {"targets": {"QQQM": 50.0}},
            "portfolio": {
                "market_values": {"QQQM": 0.0},
                "liquid_cash": 100.0,
            },
            "execution": {"current_min_trade": 1.0, "investable_cash": 100.0},
        },
        market_data_port=FakeMarketDataPort({"QQQM": 500.0}),
        execution_port=execution_port,
        dry_run_only=True,
        notional_buy_execution=True,
    )

    assert result.action_done is True
    assert len(execution_port.orders) == 1
    order = execution_port.orders[0]
    assert order.side == "buy"
    assert order.symbol == "QQQM"
    assert order.order_type == "market"
    assert order.metadata["notional_usd"] == 50.0
    assert result.execution_notes == ()


def test_notional_buy_keeps_cash_buffer_when_order_would_use_all_available_cash():
    execution_port = FakeExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {"targets": {"IBIT": 150.0}},
            "portfolio": {
                "market_values": {"IBIT": 70.0},
                "quantities": {"IBIT": 2.0},
                "liquid_cash": 80.0,
                "total_equity": 150.0,
            },
            "execution": {"current_min_trade": 1.0, "investable_cash": 80.0},
        },
        market_data_port=FakeMarketDataPort({"IBIT": 35.0}),
        execution_port=execution_port,
        dry_run_only=False,
        notional_buy_execution=True,
    )

    assert result.action_done is True
    assert execution_port.orders[0].metadata["notional_usd"] == 78.4


def test_notional_cash_buffer_preserves_minimum_eligible_order():
    assert _apply_notional_cash_buffer(buy_budget=1.02, investable_cash=1.02) == 1.0


def test_execute_value_target_plan_routes_rejected_notional_buy_to_skipped_orders():
    class RejectedExecutionPort(FakeExecutionPort):
        def submit_order(self, order_intent) -> ExecutionReport:
            self.orders.append(order_intent)
            return ExecutionReport(
                symbol=order_intent.symbol,
                side=order_intent.side,
                quantity=order_intent.quantity,
                status="rejected",
                raw_payload={
                    "statusCode": 400,
                    "error": "Bad Request",
                    "message": (
                        "Fractional Shares Trading Disclosure must be accepted before placing order."
                    ),
                    "refCode": 1219,
                },
            )

    execution_port = RejectedExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {"targets": {"IBIT": 150.0}},
            "portfolio": {
                "market_values": {"IBIT": 70.0},
                "quantities": {"IBIT": 2.0},
                "liquid_cash": 80.0,
                "total_equity": 150.0,
            },
            "execution": {"current_min_trade": 1.0, "investable_cash": 80.0},
        },
        market_data_port=FakeMarketDataPort({"IBIT": 35.0}),
        execution_port=execution_port,
        dry_run_only=False,
        notional_buy_execution=True,
    )

    assert result.action_done is False
    assert result.submitted_orders == ()
    assert result.skipped_orders[0]["reason"] == "fractional_trading_disclosure_required"
    assert result.skipped_orders[0]["notional_usd"] == 78.4
    assert result.execution_notes == ()


def test_execute_value_target_plan_notional_buy_skips_below_minimum():
    execution_port = FakeExecutionPort()
    result = execute_value_target_plan(
        plan={
            "allocation": {"targets": {"QQQM": 0.5}},
            "portfolio": {
                "market_values": {"QQQM": 0.0},
                "liquid_cash": 100.0,
            },
            "execution": {"current_min_trade": 0.0, "investable_cash": 100.0},
        },
        market_data_port=FakeMarketDataPort({"QQQM": 500.0}),
        execution_port=execution_port,
        dry_run_only=True,
        notional_buy_execution=True,
    )

    assert result.action_done is False
    assert execution_port.orders == []
    assert result.skipped_orders == (
        {
            "symbol": "QQQM",
            "reason": "buy_notional_below_minimum",
            "notional_usd": 0.5,
            "min_notional_usd": 1.0,
        },
    )


def test_execute_value_target_plan_notional_buy_preserves_sub_share_budget():
    plan = {
        "allocation": {"targets": {"SPY": 50.0}},
        "portfolio": {
            "market_values": {"SPY": 0.0},
            "liquid_cash": 100.0,
        },
        "execution": {"current_min_trade": 1.0, "investable_cash": 100.0},
    }
    market_data_port = FakeMarketDataPort({"SPY": 500.0})

    whole_share_port = FakeExecutionPort()
    whole_share_result = execute_value_target_plan(
        plan=plan,
        market_data_port=market_data_port,
        execution_port=whole_share_port,
        dry_run_only=True,
        notional_buy_execution=False,
    )

    notional_port = FakeExecutionPort()
    notional_result = execute_value_target_plan(
        plan=plan,
        market_data_port=market_data_port,
        execution_port=notional_port,
        dry_run_only=True,
        notional_buy_execution=True,
    )

    assert whole_share_result.action_done is False
    assert whole_share_port.orders == []
    assert notional_result.action_done is True
    assert notional_port.orders[0].metadata["notional_usd"] == 50.0
