from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from application.execution_service import (
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
            raw_payload={
                "limit_price": order_intent.limit_price,
                "max_notional_usd": order_intent.metadata.get("max_notional_usd"),
            },
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
    )

    assert result.action_done is True
    assert [(order.side, order.symbol, order.quantity) for order in execution_port.orders] == [
        ("buy", "SPY", 5.0),
    ]
    assert execution_port.orders[0].metadata == {}


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
    )

    assert result.action_done is True
    assert [(order.side, order.symbol, order.quantity) for order in execution_port.orders] == [
        ("sell", "SOXX", 1.0),
        ("buy", "SOXL", 1.0),
    ]
