from __future__ import annotations

from pathlib import Path


def test_sync_cloud_run_env_workflow_syncs_strategy_plugin_alert_settings():
    workflow_path = Path(__file__).resolve().parents[1] / ".github/workflows/sync-cloud-run-env.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    for name in (
        "CLOUD_SCHEDULER_LOCATION",
        "CLOUD_SCHEDULER_MAIN_TIME",
        "CLOUD_SCHEDULER_PROBE_TIME",
        "CLOUD_SCHEDULER_PRECHECK_TIME",
    ):
        assert f"{name}: ${{{{ vars.{name} }}}}" in workflow

    for name in (
        "STRATEGY_PLUGIN_ALERT_CHANNELS",
        "STRATEGY_PLUGIN_ALERT_EMAIL_RECIPIENTS",
        "STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_EMAIL",
        "STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_HOST",
        "STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_PORT",
        "STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_SECURITY",
        "STRATEGY_PLUGIN_ALERT_SMS_RECIPIENTS",
        "STRATEGY_PLUGIN_ALERT_SMS_PROVIDER",
        "STRATEGY_PLUGIN_ALERT_SMS_ACCOUNT_ID",
        "STRATEGY_PLUGIN_ALERT_SMS_SENDER",
        "STRATEGY_PLUGIN_ALERT_SMS_MESSAGING_SERVICE_ID",
        "STRATEGY_PLUGIN_ALERT_SMS_API_BASE_URL",
        "STRATEGY_PLUGIN_ALERT_SMS_BODY_MAX_CHARS",
        "STRATEGY_PLUGIN_ALERT_PUSH_RECIPIENTS",
        "STRATEGY_PLUGIN_ALERT_PUSH_PROVIDER",
        "STRATEGY_PLUGIN_ALERT_PUSH_API_BASE_URL",
        "STRATEGY_PLUGIN_ALERT_PUSH_DEVICE",
        "STRATEGY_PLUGIN_ALERT_PUSH_PRIORITY",
        "STRATEGY_PLUGIN_ALERT_PUSH_TAGS",
        "STRATEGY_PLUGIN_ALERT_PUSH_BODY_MAX_CHARS",
        "STRATEGY_PLUGIN_ALERT_TELEGRAM_CHAT_IDS",
        "STRATEGY_PLUGIN_ALERT_TELEGRAM_API_BASE_URL",
        "STRATEGY_PLUGIN_ALERT_TELEGRAM_PARSE_MODE",
        "STRATEGY_PLUGIN_ALERT_TELEGRAM_DISABLE_WEB_PAGE_PREVIEW",
        "STRATEGY_PLUGIN_ALERT_TELEGRAM_BODY_MAX_CHARS",
    ):
        assert f"{name}: ${{{{ vars.{name} }}}}" in workflow
        assert f"add_optional_env {name}" in workflow

    for name in (
        "INCOME_LAYER_ENABLED",
        "INCOME_LAYER_START_USD",
        "INCOME_LAYER_MAX_RATIO",
    ):
        assert f"{name}: ${{{{ vars.{name} }}}}" in workflow
        assert f"add_optional_env {name}" in workflow

    assert (
        "STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD_SECRET_NAME: "
        "${{ vars.STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD_SECRET_NAME }}"
    ) in workflow
    assert "STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD: ${{ secrets.STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD }}" in workflow
    assert (
        "add_optional_secret STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD "
        "STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD_SECRET_NAME STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD"
    ) in workflow
    assert (
        "STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN_SECRET_NAME: "
        "${{ vars.STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN_SECRET_NAME }}"
    ) in workflow
    assert "STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN: ${{ secrets.STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN }}" in workflow
    assert (
        "add_optional_secret STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN "
        "STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN_SECRET_NAME STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN"
    ) in workflow
    assert (
        "STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN_SECRET_NAME: "
        "${{ vars.STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN_SECRET_NAME }}"
    ) in workflow
    assert (
        "STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN_SECRET_NAME: "
        "${{ vars.STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN_SECRET_NAME }}"
    ) in workflow
    assert "STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN: ${{ secrets.STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN }}" in workflow
    assert (
        "STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN: ${{ secrets.STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN }}"
    ) in workflow
    assert (
        "add_optional_secret STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN "
        "STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN_SECRET_NAME STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN"
    ) in workflow
    assert (
        "add_optional_secret STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN "
        "STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN_SECRET_NAME STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN"
    ) in workflow
    assert (
        "STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN_SECRET_NAME: "
        "${{ vars.STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN_SECRET_NAME }}"
    ) in workflow
    assert (
        "STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN: "
        "${{ secrets.STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN }}"
    ) in workflow
    assert (
        "add_optional_secret STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN "
        "STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN_SECRET_NAME STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN"
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


def test_sync_cloud_run_env_workflow_syncs_scheduler_from_runtime_target():
    workflow_path = Path(__file__).resolve().parents[1] / ".github/workflows/sync-cloud-run-env.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "Sync Cloud Scheduler schedule" in workflow
    assert 'scheduler_location="${CLOUD_SCHEDULER_LOCATION:-${CLOUD_RUN_REGION}}"' in workflow
    assert 'raw_runtime_target = os.environ.get("RUNTIME_TARGET_JSON", "").strip()' in workflow
    assert 'scheduler = runtime_target.get("scheduler") if isinstance(runtime_target, dict) else {}' in workflow
    assert 'print(str(runtime_scheduler.get("timezone") or "America/New_York").strip())' in workflow
    assert 'configured_time("main_time", "CLOUD_SCHEDULER_MAIN_TIME", "45 15")' in workflow
    assert 'configured_time("probe_time", "CLOUD_SCHEDULER_PROBE_TIME", "35 9,15")' in workflow
    assert 'configured_time("precheck_time", "CLOUD_SCHEDULER_PRECHECK_TIME", "45 9")' in workflow
    assert 'scheduler_job_candidates=("${CLOUD_RUN_SERVICE}-${suffix}")' in workflow
    assert 'scheduler_job_candidates+=("${CLOUD_RUN_SERVICE%-service}-${suffix}")' in workflow
    assert 'if len(time_fields) == 5:' in workflow
    assert 'print(" ".join(time_fields))' in workflow
    assert 'print(" ".join([*time_fields, *current_fields[2:]]))' in workflow
    assert 'gcloud scheduler jobs update http "${job_name}"' in workflow
    assert '--schedule="${desired_schedule}"' in workflow
    assert '--time-zone="${market_timezone}"' in workflow
