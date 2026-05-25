from __future__ import annotations

from pathlib import Path


def test_sync_cloud_run_env_workflow_syncs_crisis_alert_settings():
    workflow_path = Path(__file__).resolve().parents[1] / ".github/workflows/sync-cloud-run-env.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    for name in (
        "CRISIS_ALERT_EMAIL_RECIPIENTS",
        "CRISIS_ALERT_EMAIL_SENDER_EMAIL",
        "CRISIS_ALERT_EMAIL_SMTP_HOST",
        "CRISIS_ALERT_EMAIL_SMTP_PORT",
        "CRISIS_ALERT_EMAIL_SMTP_SECURITY",
        "CRISIS_ALERT_SMS_RECIPIENTS",
        "CRISIS_ALERT_SMS_PROVIDER",
        "CRISIS_ALERT_SMS_ACCOUNT_ID",
        "CRISIS_ALERT_SMS_SENDER",
        "CRISIS_ALERT_SMS_MESSAGING_SERVICE_ID",
        "CRISIS_ALERT_SMS_API_BASE_URL",
        "CRISIS_ALERT_SMS_BODY_MAX_CHARS",
    ):
        assert f"{name}: ${{{{ vars.{name} }}}}" in workflow
        assert f"add_optional_env {name}" in workflow

    assert (
        "CRISIS_ALERT_EMAIL_SENDER_PASSWORD_SECRET_NAME: "
        "${{ vars.CRISIS_ALERT_EMAIL_SENDER_PASSWORD_SECRET_NAME }}"
    ) in workflow
    assert "CRISIS_ALERT_EMAIL_SENDER_PASSWORD: ${{ secrets.CRISIS_ALERT_EMAIL_SENDER_PASSWORD }}" in workflow
    assert (
        "add_optional_secret CRISIS_ALERT_EMAIL_SENDER_PASSWORD "
        "CRISIS_ALERT_EMAIL_SENDER_PASSWORD_SECRET_NAME CRISIS_ALERT_EMAIL_SENDER_PASSWORD"
    ) in workflow
    assert (
        "CRISIS_ALERT_SMS_AUTH_TOKEN_SECRET_NAME: "
        "${{ vars.CRISIS_ALERT_SMS_AUTH_TOKEN_SECRET_NAME }}"
    ) in workflow
    assert "CRISIS_ALERT_SMS_AUTH_TOKEN: ${{ secrets.CRISIS_ALERT_SMS_AUTH_TOKEN }}" in workflow
    assert (
        "add_optional_secret CRISIS_ALERT_SMS_AUTH_TOKEN "
        "CRISIS_ALERT_SMS_AUTH_TOKEN_SECRET_NAME CRISIS_ALERT_SMS_AUTH_TOKEN"
    ) in workflow
    assert '"CRISIS_ALERT_GOOGLE_VOICE_TO"' in workflow
    assert '"CRISIS_ALERT_GOOGLE_VOICE_GATEWAY"' in workflow
    assert '"CRISIS_ALERT_GOOGLE_VOICE_GMAIL_USER"' in workflow
    assert '"CRISIS_ALERT_GOOGLE_VOICE_GMAIL_APP_PASSWORD"' in workflow
    assert '"CRISIS_ALERT_GOOGLE_VOICE_RECIPIENTS"' in workflow
    assert '"CRISIS_ALERT_GOOGLE_VOICE_SENDER_EMAIL"' in workflow
    assert '"CRISIS_ALERT_GOOGLE_VOICE_SENDER_PASSWORD"' in workflow
    assert '"CRISIS_ALERT_GOOGLE_VOICE_SMTP_HOST"' in workflow
    assert '"CRISIS_ALERT_GOOGLE_VOICE_SMTP_PORT"' in workflow
    assert '"CRISIS_ALERT_GOOGLE_VOICE_SMTP_SECURITY"' in workflow
    assert '"CRISIS_ALERT_SMTP_HOST"' in workflow
