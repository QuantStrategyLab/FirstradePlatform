from __future__ import annotations

import subprocess

from scripts import cloud_run_runtime_guard as guard


def test_telegram_targets_prefer_strategy_plugin_alert_secret(monkeypatch):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TG_TOKEN", raising=False)
    monkeypatch.delenv("STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv(
        "STRATEGY_PLUGIN_ALERT_TELEGRAM_CHAT_IDS",
        "strategy-chat; backup-chat",
    )
    monkeypatch.setenv(
        "STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN_SECRET_NAME",
        "strategy-plugin-telegram-token",
    )
    monkeypatch.setenv("GLOBAL_TELEGRAM_CHAT_ID", "platform-chat")
    monkeypatch.setenv("TELEGRAM_TOKEN_SECRET_NAME", "platform-telegram-token")
    monkeypatch.setenv("GCP_PROJECT_ID", "firstradequant")
    observed = []

    def fake_run_gcloud(command):
        observed.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="strategy-token\n",
            stderr="",
        )

    monkeypatch.setattr(guard, "_run_gcloud", fake_run_gcloud)

    assert guard._telegram_targets() == [
        ("strategy-token", "strategy-chat"),
        ("strategy-token", "backup-chat"),
    ]
    assert observed == [
        [
            "gcloud",
            "secrets",
            "versions",
            "access",
            "latest",
            "--secret",
            "strategy-plugin-telegram-token",
            "--project",
            "firstradequant",
        ]
    ]
