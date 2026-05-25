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
    assert settings.crisis_alert_google_voice_recipients == ()
    assert settings.crisis_alert_google_voice_sender_email is None
    assert settings.crisis_alert_google_voice_sender_password is None
    assert settings.crisis_alert_google_voice_smtp_host is None
    assert settings.crisis_alert_google_voice_smtp_port is None
    assert settings.crisis_alert_google_voice_smtp_security is None


def test_reserved_cash_policy_loads_from_env(monkeypatch):
    monkeypatch.setenv("RUNTIME_TARGET_JSON", _target_json())
    monkeypatch.setenv("FIRSTRADE_MIN_RESERVED_CASH_USD", "250")
    monkeypatch.setenv("FIRSTRADE_RESERVED_CASH_RATIO", "0.025")

    settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

    assert settings.reserved_cash_floor_usd == 250.0
    assert settings.reserved_cash_ratio == 0.025


def test_crisis_alert_google_voice_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("RUNTIME_TARGET_JSON", _target_json())
    monkeypatch.setenv("CRISIS_ALERT_GOOGLE_VOICE_RECIPIENTS", "alerts@example.com; voice@example.com")
    monkeypatch.setenv("CRISIS_ALERT_GOOGLE_VOICE_SENDER_EMAIL", "sender@example.com")
    monkeypatch.setenv("CRISIS_ALERT_GOOGLE_VOICE_SENDER_PASSWORD", "secret")
    monkeypatch.setenv("CRISIS_ALERT_GOOGLE_VOICE_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("CRISIS_ALERT_GOOGLE_VOICE_SMTP_PORT", "587")
    monkeypatch.setenv("CRISIS_ALERT_GOOGLE_VOICE_SMTP_SECURITY", "starttls")

    settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

    assert settings.crisis_alert_google_voice_recipients == ("alerts@example.com", "voice@example.com")
    assert settings.crisis_alert_google_voice_sender_email == "sender@example.com"
    assert settings.crisis_alert_google_voice_sender_password == "secret"
    assert settings.crisis_alert_google_voice_smtp_host == "smtp.example.com"
    assert settings.crisis_alert_google_voice_smtp_port == "587"
    assert settings.crisis_alert_google_voice_smtp_security == "starttls"


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
