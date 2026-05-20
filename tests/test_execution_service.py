from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from application.execution_service import execute_value_target_plan
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
                "max_notional_usd": order_intent.metadata["max_notional_usd"],
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
