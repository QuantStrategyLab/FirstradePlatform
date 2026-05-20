from __future__ import annotations

from strategy_loader import load_strategy_runtime_adapter_for_profile
from strategy_registry import (
    FIRSTRADE_PLATFORM,
    get_platform_profile_status_matrix,
    get_supported_profiles_for_platform,
)


def test_firstrade_strategy_registry_uses_native_platform_adapter():
    adapter = load_strategy_runtime_adapter_for_profile("global_etf_rotation")

    assert adapter.available_inputs == frozenset({"market_history", "portfolio_snapshot"})
    assert adapter.portfolio_input_name == "portfolio_snapshot"


def test_profile_status_matrix_reports_firstrade_without_bridge_metadata():
    rows = get_platform_profile_status_matrix()

    assert rows
    assert all(row["platform"] == FIRSTRADE_PLATFORM for row in rows)
    assert all("strategy_adapter_source_platform" not in row for row in rows)
    assert "global_etf_rotation" in get_supported_profiles_for_platform(FIRSTRADE_PLATFORM)

