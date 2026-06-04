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
    assert settings.strategy_plugin_alert_channels == ()
    assert settings.strategy_plugin_alert_email_recipients == ()
    assert settings.strategy_plugin_alert_email_sender_email is None
    assert settings.strategy_plugin_alert_email_sender_password is None
    assert settings.strategy_plugin_alert_email_smtp_host is None
    assert settings.strategy_plugin_alert_email_smtp_port is None
    assert settings.strategy_plugin_alert_email_smtp_security is None
    assert settings.strategy_plugin_alert_sms_recipients == ()
    assert settings.strategy_plugin_alert_sms_provider is None
    assert settings.strategy_plugin_alert_sms_account_id is None
    assert settings.strategy_plugin_alert_sms_auth_token is None
    assert settings.strategy_plugin_alert_sms_sender is None
    assert settings.strategy_plugin_alert_sms_messaging_service_id is None
    assert settings.strategy_plugin_alert_sms_api_base_url is None
    assert settings.strategy_plugin_alert_sms_body_max_chars is None
    assert settings.strategy_plugin_alert_push_recipients == ()
    assert settings.strategy_plugin_alert_push_provider is None
    assert settings.strategy_plugin_alert_push_app_token is None
    assert settings.strategy_plugin_alert_push_access_token is None
    assert settings.strategy_plugin_alert_push_api_base_url is None
    assert settings.strategy_plugin_alert_push_device is None
    assert settings.strategy_plugin_alert_push_priority is None
    assert settings.strategy_plugin_alert_push_tags is None
    assert settings.strategy_plugin_alert_push_body_max_chars is None
    assert settings.strategy_plugin_alert_telegram_chat_ids == ()
    assert settings.strategy_plugin_alert_telegram_bot_token is None
    assert settings.strategy_plugin_alert_telegram_api_base_url is None
    assert settings.strategy_plugin_alert_telegram_parse_mode is None
    assert settings.strategy_plugin_alert_telegram_disable_web_page_preview is None
    assert settings.strategy_plugin_alert_telegram_body_max_chars is None


def test_reserved_cash_policy_loads_from_env(monkeypatch):
    monkeypatch.setenv("RUNTIME_TARGET_JSON", _target_json())
    monkeypatch.setenv("FIRSTRADE_MIN_RESERVED_CASH_USD", "250")
    monkeypatch.setenv("FIRSTRADE_RESERVED_CASH_RATIO", "0.025")

    settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

    assert settings.reserved_cash_floor_usd == 250.0
    assert settings.reserved_cash_ratio == 0.025


def test_strategy_plugin_alert_email_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("RUNTIME_TARGET_JSON", _target_json())
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_EMAIL_RECIPIENTS", "alerts@example.com; voice@example.com")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_EMAIL", "sender@example.com")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD", "secret")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_PORT", "587")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_SECURITY", "starttls")

    settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

    assert settings.strategy_plugin_alert_email_recipients == ("alerts@example.com", "voice@example.com")
    assert settings.strategy_plugin_alert_email_sender_email == "sender@example.com"
    assert settings.strategy_plugin_alert_email_sender_password == "secret"
    assert settings.strategy_plugin_alert_email_smtp_host == "smtp.example.com"
    assert settings.strategy_plugin_alert_email_smtp_port == "587"
    assert settings.strategy_plugin_alert_email_smtp_security == "starttls"


def test_strategy_plugin_alert_sms_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("RUNTIME_TARGET_JSON", _target_json())
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_SMS_RECIPIENTS", "+15165480265;(516) 548-0265")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_SMS_PROVIDER", "twilio")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_SMS_ACCOUNT_ID", "AC123")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN", "secret")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_SMS_SENDER", "+15551234567")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_SMS_MESSAGING_SERVICE_ID", "MG123")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_SMS_API_BASE_URL", "https://twilio.example.test")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_SMS_BODY_MAX_CHARS", "160")

    settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

    assert settings.strategy_plugin_alert_sms_recipients == ("+15165480265", "(516) 548-0265")
    assert settings.strategy_plugin_alert_sms_provider == "twilio"
    assert settings.strategy_plugin_alert_sms_account_id == "AC123"
    assert settings.strategy_plugin_alert_sms_auth_token == "secret"
    assert settings.strategy_plugin_alert_sms_sender == "+15551234567"
    assert settings.strategy_plugin_alert_sms_messaging_service_id == "MG123"
    assert settings.strategy_plugin_alert_sms_api_base_url == "https://twilio.example.test"
    assert settings.strategy_plugin_alert_sms_body_max_chars == "160"


def test_strategy_plugin_alert_channels_and_push_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("RUNTIME_TARGET_JSON", _target_json())
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_CHANNELS", "email;push;telegram")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_PUSH_RECIPIENTS", "risk-topic; backup-topic")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_PUSH_PROVIDER", "ntfy")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN", "app-token")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN", "access-token")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_PUSH_API_BASE_URL", "https://ntfy.example.test")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_PUSH_DEVICE", "iphone")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_PUSH_PRIORITY", "5")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_PUSH_TAGS", "warning")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_PUSH_BODY_MAX_CHARS", "300")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_TELEGRAM_CHAT_IDS", "12345; @risk_channel")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_TELEGRAM_API_BASE_URL", "https://telegram.example.test")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_TELEGRAM_PARSE_MODE", "HTML")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_TELEGRAM_DISABLE_WEB_PAGE_PREVIEW", "false")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_TELEGRAM_BODY_MAX_CHARS", "900")

    settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

    assert settings.strategy_plugin_alert_channels == ("email", "push", "telegram")
    assert settings.strategy_plugin_alert_push_recipients == ("risk-topic", "backup-topic")
    assert settings.strategy_plugin_alert_push_provider == "ntfy"
    assert settings.strategy_plugin_alert_push_app_token == "app-token"
    assert settings.strategy_plugin_alert_push_access_token == "access-token"
    assert settings.strategy_plugin_alert_push_api_base_url == "https://ntfy.example.test"
    assert settings.strategy_plugin_alert_push_device == "iphone"
    assert settings.strategy_plugin_alert_push_priority == "5"
    assert settings.strategy_plugin_alert_push_tags == "warning"
    assert settings.strategy_plugin_alert_push_body_max_chars == "300"
    assert settings.strategy_plugin_alert_telegram_chat_ids == ("12345", "@risk_channel")
    assert settings.strategy_plugin_alert_telegram_bot_token == "telegram-token"
    assert settings.strategy_plugin_alert_telegram_api_base_url == "https://telegram.example.test"
    assert settings.strategy_plugin_alert_telegram_parse_mode == "HTML"
    assert settings.strategy_plugin_alert_telegram_disable_web_page_preview == "false"
    assert settings.strategy_plugin_alert_telegram_body_max_chars == "900"


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
