from __future__ import annotations

from runtime_config_support import PlatformRuntimeSettings
from strategy_runtime import _build_runtime_overrides


def _runtime_settings(**overrides) -> PlatformRuntimeSettings:
    values = {
        "project_id": None,
        "account_prefix": "FIRSTRADE",
        "account_region": "US",
        "strategy_profile": "russell_top50_leader_rotation",
        "strategy_display_name": "Mega Cap Leader Rotation Top 50 Balanced",
        "strategy_domain": "us_equity",
        "notify_lang": "en",
        "tg_token": None,
        "tg_chat_id": None,
        "dry_run_only": True,
        "live_trading_enabled": False,
        "run_strategy_on_http": False,
        "live_order_ack": False,
        "max_order_notional_usd": None,
    }
    values.update(overrides)
    return PlatformRuntimeSettings(**values)


def test_runtime_execution_window_override_applies_to_mega_strategy():
    settings = _runtime_settings(runtime_execution_window_trading_days=7)

    assert _build_runtime_overrides(
        "russell_top50_leader_rotation",
        settings,
    ) == {"runtime_execution_window_trading_days": 7}


def test_runtime_execution_window_override_applies_to_tech_strategy():
    settings = _runtime_settings(runtime_execution_window_trading_days=7)

    assert _build_runtime_overrides(
        "tech_communication_pullback_enhancement",
        settings,
    ) == {"runtime_execution_window_trading_days": 7}


def test_runtime_execution_window_override_ignores_other_profiles():
    settings = _runtime_settings(runtime_execution_window_trading_days=7)

    assert _build_runtime_overrides("global_etf_rotation", settings) == {}


def test_income_layer_overrides_apply_to_runtime_config():
    settings = _runtime_settings(
        income_layer_enabled=False,
        income_layer_start_usd=250000.0,
        income_layer_max_ratio=0.25,
    )

    assert _build_runtime_overrides("global_etf_rotation", settings) == {
        "income_layer_enabled": False,
        "income_layer_start_usd": 250000.0,
        "income_layer_max_ratio": 0.25,
    }


def test_dca_overrides_apply_to_runtime_config():
    settings = _runtime_settings(
        strategy_profile="nasdaq_sp500_smart_dca",
        dca_mode="smart",
        dca_base_investment_usd=500.0,
    )

    assert _build_runtime_overrides("nasdaq_sp500_smart_dca", settings) == {
        "investment_amount_mode": "fixed",
        "smart_multiplier_enabled": True,
        "base_investment_usd": 500.0,
    }


def test_ibit_zscore_exit_overrides_apply_to_runtime_config():
    settings = _runtime_settings(
        strategy_profile="ibit_smart_dca",
        ibit_zscore_exit_enabled=True,
        ibit_zscore_exit_mode="live",
        ibit_zscore_exit_parking_symbol="BOXX",
        ibit_zscore_exit_risk_reduced_exposure=0.5,
        ibit_zscore_exit_risk_off_exposure=0.25,
        ibit_zscore_exit_allow_outside_execution_window=True,
    )

    assert _build_runtime_overrides("ibit_smart_dca", settings) == {
        "ibit_zscore_exit_enabled": True,
        "ibit_zscore_exit_mode": "live",
        "ibit_zscore_exit_parking_symbol": "BOXX",
        "ibit_zscore_exit_risk_reduced_exposure": 0.5,
        "ibit_zscore_exit_risk_off_exposure": 0.25,
        "ibit_zscore_exit_allow_outside_execution_window": True,
    }


def test_reserved_cash_policy_overrides_apply_to_runtime_config():
    settings = _runtime_settings(
        strategy_profile="soxl_soxx_trend_income",
        reserved_cash_floor_usd=150.0,
        reserved_cash_ratio=0.03,
    )

    assert _build_runtime_overrides("soxl_soxx_trend_income", settings) == {
        "reserved_cash_floor_usd": 150.0,
        "reserved_cash_ratio": 0.03,
    }


def test_dca_overrides_ignore_non_dca_profiles():
    settings = _runtime_settings(dca_mode="smart", dca_base_investment_usd=500.0)

    assert _build_runtime_overrides("global_etf_rotation", settings) == {}


def test_stamp_portfolio_risk_metadata_calls_qpk_helper(monkeypatch):
    from datetime import datetime, timezone
    from unittest.mock import MagicMock

    from quant_platform_kit.common.models import PortfolioSnapshot
    from quant_platform_kit.common.strategy_contracts import (
    StrategyDecision,
    StrategyManifest,
    StrategyRuntimeAdapter,
)
    import strategy_runtime as strategy_runtime_module

    class _Entrypoint:
        manifest = StrategyManifest(
            profile="global_etf_rotation",
            domain="us_equity",
            display_name="Global ETF",
            description="test",
            required_inputs=frozenset({"portfolio_snapshot"}),
        )

        def evaluate(self, ctx):
            self.ctx = ctx
            return StrategyDecision()

    entrypoint = _Entrypoint()
    runtime = strategy_runtime_module.LoadedStrategyRuntime(
        entrypoint=entrypoint,
        runtime_adapter=StrategyRuntimeAdapter(portfolio_input_name="portfolio_snapshot"),
        runtime_settings=_runtime_settings(strategy_profile="global_etf_rotation"),
        logger=lambda _msg: None,
    )
    snapshot = PortfolioSnapshot(
        as_of=datetime.now(timezone.utc),
        total_equity=1_000.0,
        positions=(),
        metadata={},
    )
    stamped = PortfolioSnapshot(
        as_of=snapshot.as_of,
        total_equity=snapshot.total_equity,
        positions=(),
        metadata={"consecutive_losses": 2},
    )
    stamp = MagicMock(return_value=stamped)
    monkeypatch.setattr(
        "quant_platform_kit.strategy_lifecycle.live_equity.stamp_consecutive_losses_on_snapshot",
        stamp,
    )
    monkeypatch.setattr(
        strategy_runtime_module,
        "resolve_external_market_signal_inputs",
        lambda **_kwargs: {},
    )

    runtime.evaluate(translator=lambda key, **_kwargs: key, portfolio_snapshot=snapshot)

    stamp.assert_called_once()
    assert entrypoint.ctx.portfolio.metadata["consecutive_losses"] == 2
