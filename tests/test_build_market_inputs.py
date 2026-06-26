from __future__ import annotations

from types import SimpleNamespace

from application.rebalance_service import build_market_inputs


class _FakeMarketDataPort:
    def get_price_series(self, _symbol):
        raise AssertionError("broker market history should not be queried")


def test_ibit_smart_mode_skips_broker_market_history_loader():
    inputs = build_market_inputs(
        available_inputs={"derived_indicators", "market_history", "portfolio_snapshot"},
        market_data_port=_FakeMarketDataPort(),
        benchmark_symbol="QQQ",
        strategy_runtime_config={"smart_multiplier_enabled": True},
        strategy_profile="ibit_smart_dca",
        runtime_settings=SimpleNamespace(market_signal_required=False),
    )

    assert "derived_indicators" in inputs
    assert "market_history" not in inputs


def test_ibit_fixed_mode_keeps_broker_market_history_loader():
    inputs = build_market_inputs(
        available_inputs={"derived_indicators", "market_history", "portfolio_snapshot"},
        market_data_port=_FakeMarketDataPort(),
        benchmark_symbol="QQQ",
        strategy_runtime_config={"smart_multiplier_enabled": False},
        strategy_profile="ibit_smart_dca",
        runtime_settings=SimpleNamespace(market_signal_required=False),
    )

    assert callable(inputs["market_history"])
