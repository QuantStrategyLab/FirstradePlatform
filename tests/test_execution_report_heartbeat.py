from __future__ import annotations

import subprocess
import datetime as dt

from scripts import execution_report_heartbeat as heartbeat


def test_report_globs_include_sanitized_month_segments(monkeypatch):
    monkeypatch.delenv("RUNTIME_HEARTBEAT_GCS_GLOBS", raising=False)
    monkeypatch.delenv("EXECUTION_REPORT_GCS_URI", raising=False)
    monkeypatch.delenv("RUNTIME_HEARTBEAT_GCS_URIS", raising=False)
    monkeypatch.delenv("RUNTIME_HEARTBEAT_REPORT_PLATFORM", raising=False)
    monkeypatch.setenv("FIRSTRADE_GCS_STATE_BUCKET", "runtime-state")
    monkeypatch.setenv("FIRSTRADE_STATE_PREFIX", "firstrade-platform")

    globs = heartbeat._report_globs(
        dt.datetime(2026, 5, 31, tzinfo=dt.timezone.utc),
        dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),
    )

    assert globs == [
        "gs://runtime-state/firstrade-platform/strategy-runs/**/2026-05/*.json",
        "gs://runtime-state/firstrade-platform/strategy-runs/**/2026_05/*.json",
        "gs://runtime-state/firstrade-platform/strategy-runs/**/2026-06/*.json",
        "gs://runtime-state/firstrade-platform/strategy-runs/**/2026_06/*.json",
    ]

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

    monkeypatch.setattr(heartbeat, "_run_gcloud", fake_run_gcloud)

    assert heartbeat._telegram_targets() == [
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
