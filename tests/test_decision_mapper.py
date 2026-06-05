from __future__ import annotations

from datetime import datetime, timezone

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.strategy_contracts import PositionTarget, StrategyDecision

from decision_mapper import map_strategy_decision_to_plan


def test_applies_platform_reserved_cash_policy_to_weight_decision():
    decision = StrategyDecision(
        positions=(
            PositionTarget(symbol="AAPL", target_weight=0.5),
            PositionTarget(symbol="MSFT", target_weight=0.5),
        ),
        diagnostics={"signal_description": "risk on"},
    )
    snapshot = PortfolioSnapshot(
        as_of=datetime.now(timezone.utc),
        total_equity=20000.0,
        buying_power=4000.0,
        positions=(Position(symbol="AAPL", quantity=1, market_value=1000.0),),
    )

    plan = map_strategy_decision_to_plan(
        decision,
        snapshot=snapshot,
        strategy_profile="mega_cap_leader_rotation_top50_balanced",
        runtime_metadata={
            "firstrade_execution_policy": {
                "reserved_cash_floor_usd": 1500.0,
                "reserved_cash_ratio": 0.03,
            }
        },
    )

    assert plan["execution"]["reserved_cash"] == 1500.0
    assert plan["execution"]["investable_cash"] == 2500.0


def test_platform_reserved_cash_policy_does_not_lower_strategy_reserve():
    decision = StrategyDecision(
        positions=(PositionTarget(symbol="AAA", target_value=5000.0),),
        diagnostics={
            "execution_annotations": {
                "trade_threshold_value": 100.0,
                "reserved_cash": 1200.0,
            }
        },
    )
    snapshot = PortfolioSnapshot(
        as_of=datetime.now(timezone.utc),
        total_equity=10000.0,
        buying_power=3000.0,
        positions=(),
    )

    plan = map_strategy_decision_to_plan(
        decision,
        snapshot=snapshot,
        strategy_profile="tqqq_growth_income",
        runtime_metadata={
            "firstrade_execution_policy": {
                "reserved_cash_floor_usd": 150.0,
                "reserved_cash_ratio": 0.03,
            }
        },
    )

    assert plan["execution"]["reserved_cash"] == 1200.0
    assert plan["execution"]["investable_cash"] == 1800.0


def test_value_decision_without_threshold_uses_platform_default():
    decision = StrategyDecision(
        positions=(PositionTarget(symbol="AAA", target_value=500.0),),
        diagnostics={"signal_display": "hold AAA"},
    )
    snapshot = PortfolioSnapshot(
        as_of=datetime.now(timezone.utc),
        total_equity=20000.0,
        buying_power=3000.0,
        positions=(),
    )

    plan = map_strategy_decision_to_plan(
        decision,
        snapshot=snapshot,
        strategy_profile="mega_cap_leader_rotation_top50_balanced",
    )

    assert plan["execution"]["trade_threshold_value"] == 200.0
    assert plan["execution"]["current_min_trade"] == 200.0
    assert plan["allocation"]["targets"]["AAA"] == 500.0


def test_no_execute_decision_without_threshold_holds_current_positions():
    decision = StrategyDecision(
        positions=(),
        risk_flags=("no_execute",),
        diagnostics={"signal_description": "no actionable signal"},
    )
    snapshot = PortfolioSnapshot(
        as_of=datetime.now(timezone.utc),
        total_equity=5000.0,
        buying_power=1000.0,
        positions=(Position(symbol="AAA", quantity=3, market_value=750.0),),
    )

    plan = map_strategy_decision_to_plan(
        decision,
        snapshot=snapshot,
        strategy_profile="mega_cap_leader_rotation_top50_balanced",
    )

    assert plan["execution"]["trade_threshold_value"] == 100.0
    assert plan["execution"]["current_min_trade"] == 100.0
    assert plan["allocation"]["targets"]["AAA"] == 750.0
