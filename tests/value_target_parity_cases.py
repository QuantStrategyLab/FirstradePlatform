"""Frozen inputs for the B12 value-target facade parity test.

The golden file ``tests/fixtures/value_target_plan_parity.json`` was produced by
running ``map_strategy_decision_to_plan`` on these exact inputs with the legacy
``build_value_target_runtime_plan`` + mixed ``ValueTargetExecutionAnnotations``
path (FirstradePlatform main@a314852, QPK@350dd38). The migrated
``semantics=`` / ``display=`` path must reproduce it byte-for-byte after
canonical JSON encoding.

Regenerate only when an intentional behavior change is reviewed:
    python tests/value_target_parity_cases.py --write
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.common.strategy_contracts import PositionTarget, StrategyDecision

GOLDEN_PATH = Path(__file__).with_name("fixtures") / "value_target_plan_parity.json"
_AS_OF = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)

_FULL_ANNOTATIONS = {
    "trade_threshold_value": 150.0,
    "reserved_cash": 250.0,
    "signal_display": "risk on",
    "status_display": "trend up",
    "dashboard_text": "dash",
    "separator": "---",
    "signal_date": "2026-10-08",
    "effective_date": "2026-10-09",
    "execution_timing_contract": "next_session_close",
    "execution_calendar_source": "xnys",
    "signal_effective_after_trading_days": 1,
    "benchmark_symbol": "SOXX",
    "benchmark_price": 251.5,
    "long_trend_value": 240.25,
    "exit_line": 230.0,
    "deploy_ratio_text": "60%",
    "income_ratio_text": "20%",
    "income_locked_ratio_text": "5%",
    "active_risk_asset": "SOXL",
    "current_min_trade": 175.0,
    "investable_cash": 1800.0,
}


def _snapshot(
    *,
    total_equity: float,
    buying_power: float | None,
    cash_balance: float | None = None,
    positions: tuple[tuple[str, float, float], ...] = (),
    metadata: dict[str, Any] | None = None,
) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        as_of=_AS_OF,
        total_equity=total_equity,
        buying_power=buying_power,
        cash_balance=cash_balance,
        positions=tuple(
            Position(symbol=symbol, quantity=quantity, market_value=market_value)
            for symbol, quantity, market_value in positions
        ),
        metadata=dict(metadata or {}),
    )


def build_cases() -> list[tuple[str, dict[str, Any]]]:
    soxl_positions = (("SOXL", 40.0, 4000.0), ("BOXX", 10.0, 1100.0), ("QQQI", 5.0, 260.0))
    return [
        (
            "value_full_annotations_soxl",
            dict(
                decision=StrategyDecision(
                    positions=(
                        PositionTarget(symbol="SOXL", target_value=5000.0),
                        PositionTarget(symbol="BOXX", target_value=2000.0, role="safe_haven"),
                        PositionTarget(symbol="QQQI", target_value=800.0, role="income"),
                    ),
                    diagnostics={
                        "execution_annotations": dict(_FULL_ANNOTATIONS),
                        "blend_gate_volatility_delever_threshold_mode": "rolling_percentile",
                        "blend_gate_volatility_delever_metric": 0.61,
                        "market_regime_control_route": "risk_reduced",
                        "market_regime_control_reason_codes": ("macro:vix_crisis_level",),
                    },
                ),
                snapshot=_snapshot(
                    total_equity=10000.0,
                    buying_power=3000.0,
                    positions=soxl_positions,
                    metadata={"cash_by_currency": {"USD": 3000.0}},
                ),
                strategy_profile="soxl_soxx_trend_income",
                runtime_metadata={
                    "firstrade_execution_policy": {
                        "reserved_cash_floor_usd": 100.0,
                        "reserved_cash_ratio": 0.05,
                    }
                },
            ),
        ),
        (
            "value_legacy_keys_tqqq",
            dict(
                decision=StrategyDecision(
                    positions=(
                        PositionTarget(symbol="TQQQ", target_value=4000.0),
                        PositionTarget(symbol="QQQM", target_value=1000.0),
                        PositionTarget(symbol="BOXX", target_value=3000.0, role="safe_haven"),
                    ),
                    diagnostics={
                        "threshold": 120.0,
                        "reserved": 80.0,
                        "signal_message": "legacy signal",
                        "market_status": "legacy status",
                        "dashboard": "legacy dash",
                        "qqq_price": 512.0,
                        "ma200": 480.0,
                        "dual_drive_volatility_delever_applied": True,
                        "dual_drive_crisis_defense_destination": "BOXX",
                    },
                ),
                snapshot=_snapshot(
                    total_equity=9000.0,
                    buying_power=2500.0,
                    positions=(("TQQQ", 30.0, 3500.0), ("BOXX", 20.0, 2200.0)),
                ),
                strategy_profile="tqqq_growth_income",
                runtime_metadata=None,
            ),
        ),
        (
            "value_without_threshold_uses_default",
            dict(
                decision=StrategyDecision(
                    positions=(PositionTarget(symbol="SOXL", target_value=6000.0),),
                    diagnostics={"signal_display": "s", "execution_annotations": {"reserved_cash": 50.0}},
                ),
                snapshot=_snapshot(total_equity=25000.0, buying_power=7000.0),
                strategy_profile="soxl_soxx_trend_income",
                runtime_metadata={"firstrade_execution_policy": {"reserved_cash_ratio": 0.02}},
            ),
        ),
        (
            "weight_translation_with_policy",
            dict(
                decision=StrategyDecision(
                    positions=(
                        PositionTarget(symbol="AAPL", target_weight=0.4),
                        PositionTarget(symbol="MSFT", target_weight=0.4),
                        PositionTarget(symbol="BOXX", target_weight=0.2, role="safe_haven"),
                    ),
                    diagnostics={
                        "signal_description": "risk on",
                        "status_description": "breadth ok",
                        "benchmark_symbol": "spy",
                        "benchmark_price": 600.5,
                        "long_trend_value": 580.0,
                        "exit_line": 570.0,
                        "execution_annotations": {
                            "dashboard_text": "weights",
                            "signal_date": "2026-10-08",
                            "effective_date": "2026-10-09",
                            "execution_timing_contract": "same_day",
                            "execution_calendar_source": "xnys",
                            "signal_effective_after_trading_days": 0,
                        },
                    },
                ),
                snapshot=_snapshot(
                    total_equity=20000.0,
                    buying_power=4000.0,
                    positions=(("AAPL", 5.0, 1000.0),),
                ),
                strategy_profile="russell_top50_leader_rotation",
                runtime_metadata={
                    "firstrade_execution_policy": {
                        "reserved_cash_floor_usd": 1500.0,
                        "reserved_cash_ratio": 0.03,
                    },
                    "execution_annotations": {"signal_date": "2026-10-07"},
                    "market_regime_control_enabled": True,
                },
            ),
        ),
        (
            "weight_translation_not_cash_only",
            dict(
                decision=StrategyDecision(
                    positions=(
                        PositionTarget(symbol="AAPL", target_weight=0.6),
                        PositionTarget(symbol="NVDA", target_weight=0.4),
                    ),
                    diagnostics={"signal_display": "fallback display", "canary_status": "canary"},
                ),
                snapshot=_snapshot(
                    total_equity=15000.0,
                    buying_power=500.0,
                    cash_balance=-200.0,
                    positions=(("AAPL", 50.0, 9000.0), ("NVDA", 30.0, 5700.0)),
                ),
                strategy_profile="russell_top50_leader_rotation",
                runtime_metadata={"firstrade_execution_policy": {"cash_only_execution": False}},
            ),
        ),
        (
            "weight_zero_equity_blocked_with_holdings",
            dict(
                decision=StrategyDecision(
                    positions=(PositionTarget(symbol="AAPL", target_weight=1.0),),
                    diagnostics={"signal_description": "risk on"},
                ),
                snapshot=_snapshot(
                    total_equity=0.0,
                    buying_power=0.0,
                    positions=(("AAPL", 1.0, 0.0),),
                ),
                strategy_profile="russell_top50_leader_rotation",
                runtime_metadata={"firstrade_execution_policy": {"reserved_cash_floor_usd": 10.0}},
            ),
        ),
        (
            "weight_zero_equity_blocked_empty",
            dict(
                decision=StrategyDecision(
                    positions=(
                        PositionTarget(symbol="AAPL", target_weight=0.5),
                        PositionTarget(symbol="QQQI", target_weight=0.5),
                    ),
                    diagnostics={},
                ),
                snapshot=_snapshot(total_equity=0.0, buying_power=0.0),
                strategy_profile="russell_top50_leader_rotation",
                runtime_metadata=None,
            ),
        ),
        (
            "no_execute_holds_current_positions",
            dict(
                decision=StrategyDecision(
                    positions=(PositionTarget(symbol="SOXL", target_value=5000.0),),
                    risk_flags=frozenset({"no_execute"}),
                    diagnostics={"signal_display": "hold", "separator": "==="},
                ),
                snapshot=_snapshot(
                    total_equity=8000.0,
                    buying_power=1000.0,
                    positions=soxl_positions,
                ),
                strategy_profile="soxl_soxx_trend_income",
                runtime_metadata={"firstrade_execution_policy": {"reserved_cash_floor_usd": 2000.0}},
            ),
        ),
        (
            "value_investable_cash_capped_by_policy",
            dict(
                decision=StrategyDecision(
                    positions=(PositionTarget(symbol="TQQQ", target_value=3000.0),),
                    diagnostics={
                        "execution_annotations": {
                            "trade_threshold_value": 100.0,
                            "reserved_cash": 10.0,
                            "investable_cash": 5000.0,
                            "current_min_trade": 90.0,
                        }
                    },
                ),
                snapshot=_snapshot(total_equity=6000.0, buying_power=1200.0),
                strategy_profile="tqqq_growth_income",
                runtime_metadata={
                    "firstrade_execution_policy": {
                        "reserved_cash_floor_usd": 300.0,
                        "reserved_cash_ratio": 0.10,
                    }
                },
            ),
        ),
    ]


def canonical(value: Any) -> Any:
    """Canonical JSON-compatible form (tuples -> lists, sets -> sorted lists)."""
    return json.loads(json.dumps(value, sort_keys=True, default=_default))


def _default(value: Any) -> Any:
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, tuple):
        return list(value)
    return repr(value)


def render_all() -> dict[str, Any]:
    from decision_mapper import map_strategy_decision_to_plan

    return {
        name: canonical(map_strategy_decision_to_plan(**kwargs))
        for name, kwargs in build_cases()
    }


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    rendered = render_all()
    text = json.dumps(rendered, indent=2, sort_keys=True) + "\n"
    if "--write" in sys.argv:
        GOLDEN_PATH.write_text(text, encoding="utf-8")
        print(f"wrote {GOLDEN_PATH}")
    else:
        sys.stdout.write(text)
