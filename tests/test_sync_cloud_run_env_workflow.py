from __future__ import annotations

from pathlib import Path


def test_sync_cloud_run_env_workflow_syncs_crisis_alert_settings():
    workflow_path = Path(__file__).resolve().parents[1] / ".github/workflows/sync-cloud-run-env.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    for name in (
        "CRISIS_ALERT_GOOGLE_VOICE_TO",
        "CRISIS_ALERT_EMAIL_TO",
        "CRISIS_ALERT_SMTP_FROM",
        "CRISIS_ALERT_EMAIL_FROM",
        "CRISIS_ALERT_SMTP_HOST",
        "CRISIS_ALERT_SMTP_PORT",
        "CRISIS_ALERT_SMTP_USERNAME",
        "CRISIS_ALERT_SMTP_STARTTLS",
        "CRISIS_ALERT_SMTP_SSL",
    ):
        assert f"{name}: ${{{{ vars.{name} }}}}" in workflow
        assert f"add_optional_env {name}" in workflow

    assert (
        "CRISIS_ALERT_SMTP_PASSWORD_SECRET_NAME: "
        "${{ vars.CRISIS_ALERT_SMTP_PASSWORD_SECRET_NAME }}"
    ) in workflow
    assert "CRISIS_ALERT_SMTP_PASSWORD: ${{ secrets.CRISIS_ALERT_SMTP_PASSWORD }}" in workflow
    assert (
        "add_optional_secret CRISIS_ALERT_SMTP_PASSWORD "
        "CRISIS_ALERT_SMTP_PASSWORD_SECRET_NAME CRISIS_ALERT_SMTP_PASSWORD"
    ) in workflow
