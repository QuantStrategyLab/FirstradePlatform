from __future__ import annotations

import pytest

from runtime_config_support import _runtime_execution_window_trading_days_env


def test_runtime_execution_window_uses_generic_env(monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS", "7")
    monkeypatch.setenv("FIRSTRADE_TECH_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS", "3")

    assert (
        _runtime_execution_window_trading_days_env("mega_cap_leader_rotation_top50_balanced")
        == 7
    )
    assert (
        _runtime_execution_window_trading_days_env("tech_communication_pullback_enhancement")
        == 7
    )


def test_runtime_execution_window_keeps_legacy_tech_env(monkeypatch):
    monkeypatch.delenv("FIRSTRADE_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS", raising=False)
    monkeypatch.setenv("FIRSTRADE_TECH_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS", "5")

    assert (
        _runtime_execution_window_trading_days_env("tech_communication_pullback_enhancement")
        == 5
    )
    assert (
        _runtime_execution_window_trading_days_env("mega_cap_leader_rotation_top50_balanced")
        is None
    )


@pytest.mark.parametrize("raw_value", ["0", "-1", "abc"])
def test_runtime_execution_window_rejects_invalid_generic_env(monkeypatch, raw_value):
    monkeypatch.setenv("FIRSTRADE_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS", raw_value)

    with pytest.raises(
        ValueError,
        match="FIRSTRADE_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS",
    ):
        _runtime_execution_window_trading_days_env("mega_cap_leader_rotation_top50_balanced")
