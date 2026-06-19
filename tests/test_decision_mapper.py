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
        strategy_profile="russell_top50_leader_rotation_aggressive",
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
            "dual_drive_volatility_delever_threshold_mode": "rolling_percentile",
            "dual_drive_volatility_delever_dynamic_threshold": 0.30,
            "dual_drive_volatility_delever_dynamic_sample_count": 252,
            "dual_drive_volatility_delever_metric": 0.312,
            "dual_drive_volatility_delever_applied": True,
            "dual_drive_volatility_delever_veto_reason": "taco_rebound_context",
            "dual_drive_volatility_delever_taco_veto_enabled": True,
            "dual_drive_volatility_delever_removed_value": 4500.0,
            "dual_drive_volatility_delever_redirect_symbol": "QQQM",
            "dual_drive_macro_risk_governor_applied": True,
            "dual_drive_macro_risk_governor_route": "risk_reduced",
            "dual_drive_crisis_defense_destination": "BOXX",
            "market_regime_control_route": "risk_reduced",
            "market_regime_control_reason_codes": ("macro:vix_crisis_level",),
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
    assert plan["execution"]["dual_drive_volatility_delever_threshold_mode"] == "rolling_percentile"
    assert plan["execution"]["dual_drive_volatility_delever_dynamic_threshold"] == 0.30
    assert plan["execution"]["dual_drive_volatility_delever_dynamic_sample_count"] == 252
    assert plan["execution"]["dual_drive_volatility_delever_metric"] == 0.312
    assert plan["execution"]["dual_drive_volatility_delever_applied"] is True
    assert plan["execution"]["dual_drive_volatility_delever_veto_reason"] == "taco_rebound_context"
    assert plan["execution"]["dual_drive_volatility_delever_taco_veto_enabled"] is True
    assert plan["execution"]["dual_drive_volatility_delever_removed_value"] == 4500.0
    assert plan["execution"]["dual_drive_volatility_delever_redirect_symbol"] == "QQQM"
    assert plan["execution"]["dual_drive_macro_risk_governor_applied"] is True
    assert plan["execution"]["dual_drive_macro_risk_governor_route"] == "risk_reduced"
    assert plan["execution"]["dual_drive_crisis_defense_destination"] == "BOXX"
    assert plan["execution"]["market_regime_control_route"] == "risk_reduced"
    assert plan["execution"]["market_regime_control_reason_codes"] == ("macro:vix_crisis_level",)


def test_maps_soxl_dynamic_volatility_fields_to_execution():
    decision = StrategyDecision(
        positions=(PositionTarget(symbol="SOXL", target_value=5000.0),),
        diagnostics={
            "blend_gate_volatility_delever_threshold_mode": "rolling_percentile",
            "blend_gate_volatility_delever_threshold": 0.60,
            "blend_gate_volatility_delever_dynamic_threshold": 0.60,
            "blend_gate_volatility_delever_dynamic_sample_count": 252,
            "blend_gate_volatility_delever_dynamic_percentile": 0.95,
            "blend_gate_volatility_delever_metric": 0.61,
            "blend_gate_volatility_delever_triggered": True,
            "blend_gate_volatility_delever_redirect_symbol": "SOXX",
            "blend_gate_volatility_delever_removed_ratio": 0.70,
            "execution_annotations": {
                "trade_threshold_value": 100.0,
                "reserved_cash": 100.0,
            },
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
        strategy_profile="soxl_soxx_trend_income",
    )

    assert plan["execution"]["blend_gate_volatility_delever_threshold_mode"] == "rolling_percentile"
    assert plan["execution"]["blend_gate_volatility_delever_dynamic_threshold"] == 0.60
    assert plan["execution"]["blend_gate_volatility_delever_dynamic_sample_count"] == 252
    assert plan["execution"]["blend_gate_volatility_delever_dynamic_percentile"] == 0.95
    assert plan["execution"]["blend_gate_volatility_delever_metric"] == 0.61
    assert plan["execution"]["blend_gate_volatility_delever_triggered"] is True
    assert plan["execution"]["blend_gate_volatility_delever_redirect_symbol"] == "SOXX"
    assert plan["execution"]["blend_gate_volatility_delever_removed_ratio"] == 0.70


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
        strategy_profile="russell_top50_leader_rotation_aggressive",
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
        strategy_profile="russell_top50_leader_rotation_aggressive",
    )

    assert plan["execution"]["trade_threshold_value"] == 100.0
    assert plan["execution"]["current_min_trade"] == 100.0
    assert plan["allocation"]["targets"]["AAA"] == 750.0
