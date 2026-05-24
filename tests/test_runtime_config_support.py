from __future__ import annotations

import pytest

from runtime_config_support import (
    _resolve_non_negative_float_env,
    _resolve_ratio_env,
    _runtime_execution_window_trading_days_env,
    load_platform_runtime_settings,
)


def _target_json(profile="mega_cap_leader_rotation_top50_balanced") -> str:
    return (
        '{"platform_id":"firstrade","strategy_profile":"'
        + profile
        + '","dry_run_only":true,"execution_mode":"paper"}'
    )


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


def test_reserved_cash_policy_defaults_to_zero(monkeypatch):
    monkeypatch.setenv("RUNTIME_TARGET_JSON", _target_json())

    settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

    assert settings.reserved_cash_floor_usd == 0.0
    assert settings.reserved_cash_ratio == 0.0
    assert settings.crisis_alert_google_voice_to == ()
    assert settings.crisis_alert_smtp_from is None
    assert settings.crisis_alert_smtp_port == 587
    assert settings.crisis_alert_smtp_starttls is True
    assert settings.crisis_alert_smtp_ssl is False


def test_reserved_cash_policy_loads_from_env(monkeypatch):
    monkeypatch.setenv("RUNTIME_TARGET_JSON", _target_json())
    monkeypatch.setenv("FIRSTRADE_MIN_RESERVED_CASH_USD", "250")
    monkeypatch.setenv("FIRSTRADE_RESERVED_CASH_RATIO", "0.025")

    settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

    assert settings.reserved_cash_floor_usd == 250.0
    assert settings.reserved_cash_ratio == 0.025


def test_crisis_alert_google_voice_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("RUNTIME_TARGET_JSON", _target_json())
    monkeypatch.setenv("CRISIS_ALERT_GOOGLE_VOICE_TO", "gateway@txt.voice.google.com")
    monkeypatch.setenv("CRISIS_ALERT_SMTP_FROM", "smtp-from@example.com")
    monkeypatch.setenv("CRISIS_ALERT_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("CRISIS_ALERT_SMTP_PORT", "465")
    monkeypatch.setenv("CRISIS_ALERT_SMTP_USERNAME", "bot")
    monkeypatch.setenv("CRISIS_ALERT_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("CRISIS_ALERT_SMTP_STARTTLS", "false")
    monkeypatch.setenv("CRISIS_ALERT_SMTP_SSL", "true")

    settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

    assert settings.crisis_alert_google_voice_to == ("gateway@txt.voice.google.com",)
    assert settings.crisis_alert_smtp_from == "smtp-from@example.com"
    assert settings.crisis_alert_smtp_host == "smtp.example.com"
    assert settings.crisis_alert_smtp_port == 465
    assert settings.crisis_alert_smtp_username == "bot"
    assert settings.crisis_alert_smtp_password == "secret"
    assert settings.crisis_alert_smtp_starttls is False
    assert settings.crisis_alert_smtp_ssl is True


def test_reserved_cash_ratio_rejects_invalid_env(monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RESERVED_CASH_RATIO", "1.25")

    with pytest.raises(ValueError, match="FIRSTRADE_RESERVED_CASH_RATIO"):
        _resolve_ratio_env("FIRSTRADE_RESERVED_CASH_RATIO", default=0.0)


@pytest.mark.parametrize("raw_value", ["nan", "inf", "-inf"])
def test_reserved_cash_floor_rejects_non_finite_env(monkeypatch, raw_value):
    monkeypatch.setenv("FIRSTRADE_MIN_RESERVED_CASH_USD", raw_value)

    with pytest.raises(ValueError, match="FIRSTRADE_MIN_RESERVED_CASH_USD must be finite"):
        _resolve_non_negative_float_env("FIRSTRADE_MIN_RESERVED_CASH_USD", default=0.0)


@pytest.mark.parametrize("raw_value", ["nan", "inf", "-inf"])
def test_reserved_cash_ratio_rejects_non_finite_env(monkeypatch, raw_value):
    monkeypatch.setenv("FIRSTRADE_RESERVED_CASH_RATIO", raw_value)

    with pytest.raises(ValueError, match="FIRSTRADE_RESERVED_CASH_RATIO must be finite"):
        _resolve_ratio_env("FIRSTRADE_RESERVED_CASH_RATIO", default=0.0)


@pytest.mark.parametrize("raw_value", ["0", "-1", "abc"])
def test_runtime_execution_window_rejects_invalid_generic_env(monkeypatch, raw_value):
    monkeypatch.setenv("FIRSTRADE_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS", raw_value)

    with pytest.raises(
        ValueError,
        match="FIRSTRADE_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS",
    ):
        _runtime_execution_window_trading_days_env("mega_cap_leader_rotation_top50_balanced")
