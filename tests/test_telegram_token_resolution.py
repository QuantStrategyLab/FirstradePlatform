from __future__ import annotations

import pytest

import runtime_config_support
from runtime_config_support import LEGACY_TELEGRAM_TOKEN_SECRET_NAME, resolve_telegram_token

UNIFIED = "quant-sentinel-telegram-bot-token"


def _reader(values):
    calls = []

    def read(name):
        calls.append(name)
        return values.get(name)

    return read, calls


def test_injected_token_wins_over_secret_manager():
    read, calls = _reader({LEGACY_TELEGRAM_TOKEN_SECRET_NAME: "legacy"})
    env = {"TELEGRAM_TOKEN": " unified-token ", "TELEGRAM_TOKEN_SECRET_NAME": UNIFIED}
    assert resolve_telegram_token(env, secret_reader=read) == "unified-token"
    assert calls == []


def test_configured_secret_name_wins_over_legacy():
    read, calls = _reader({UNIFIED: "unified", LEGACY_TELEGRAM_TOKEN_SECRET_NAME: "legacy"})
    env = {"TELEGRAM_TOKEN": "", "TELEGRAM_TOKEN_SECRET_NAME": UNIFIED}
    assert resolve_telegram_token(env, secret_reader=read) == "unified"
    assert calls == [UNIFIED]


def test_legacy_secret_is_last_fallback():
    read, calls = _reader({LEGACY_TELEGRAM_TOKEN_SECRET_NAME: "legacy"})
    env = {"TELEGRAM_TOKEN_SECRET_NAME": UNIFIED}
    assert resolve_telegram_token(env, secret_reader=read) == "legacy"
    assert calls == [UNIFIED, LEGACY_TELEGRAM_TOKEN_SECRET_NAME]


def test_legacy_only_when_nothing_configured():
    read, calls = _reader({LEGACY_TELEGRAM_TOKEN_SECRET_NAME: "legacy"})
    assert resolve_telegram_token({}, secret_reader=read) == "legacy"
    assert calls == [LEGACY_TELEGRAM_TOKEN_SECRET_NAME]


def test_missing_everywhere_returns_none():
    read, _ = _reader({})
    assert resolve_telegram_token({"TELEGRAM_TOKEN": "  "}, secret_reader=read) is None


def test_main_get_telegram_token_prefers_injected(monkeypatch):
    pytest.importorskip("flask")
    import main

    seen = []
    monkeypatch.setattr(runtime_config_support, "_read_secret", lambda name: seen.append(name) or "legacy")
    monkeypatch.setenv("TELEGRAM_TOKEN", "unified-token")
    assert main._get_telegram_token() == "unified-token"
    assert seen == []


def test_main_get_telegram_token_uses_configured_secret(monkeypatch):
    pytest.importorskip("flask")
    import main

    values = {UNIFIED: "unified", LEGACY_TELEGRAM_TOKEN_SECRET_NAME: "legacy"}
    monkeypatch.setattr(runtime_config_support, "_read_secret", lambda name: values.get(name))
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_TOKEN_SECRET_NAME", UNIFIED)
    assert main._get_telegram_token() == "unified"
