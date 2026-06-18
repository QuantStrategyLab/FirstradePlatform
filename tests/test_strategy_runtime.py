from __future__ import annotations

from runtime_config_support import PlatformRuntimeSettings
from strategy_runtime import _build_runtime_overrides


def _runtime_settings(**overrides) -> PlatformRuntimeSettings:
    values = {
        "project_id": None,
        "account_prefix": "FIRSTRADE",
        "account_region": "US",
        "strategy_profile": "mega_cap_leader_rotation_top50_balanced",
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
        "mega_cap_leader_rotation_top50_balanced",
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
