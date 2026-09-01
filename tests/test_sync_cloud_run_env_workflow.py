from __future__ import annotations

from pathlib import Path


def test_sync_cloud_run_env_workflow_requires_manual_dispatch():
    workflow_path = Path(__file__).resolve().parents[1] / ".github/workflows/sync-cloud-run-env.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "workflow_run:" not in workflow
    assert "if: github.event_name == 'workflow_dispatch'" in workflow


def test_sync_cloud_run_env_workflow_uses_sync_plan_script():
    workflow_path = Path(__file__).resolve().parents[1] / ".github/workflows/sync-cloud-run-env.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "Resolve Cloud Run sync targets" in workflow
    assert "scripts/build_cloud_run_env_sync_plan.py --json" in workflow
    assert "sync_plan_json<<__SYNC_PLAN_JSON__" in workflow
    assert "SYNC_PLAN_JSON: ${{ steps.strategy_requirements.outputs.sync_plan_json }}" in workflow
    assert "Cloud Run env sync did not resolve any targets" in workflow
    assert "Cloud Run sync target is missing service_name" in workflow
    assert "Cloud Run sync target {service_name} is missing env" in workflow
    assert (
        "CLOUD_RUN_SERVICE_TARGETS_JSON: "
        "${{ vars.CLOUD_RUN_SERVICE_TARGETS_JSON || secrets.CLOUD_RUN_SERVICE_TARGETS_JSON }}"
    ) in workflow

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
        "INCOME_LAYER_ENABLED",
        "INCOME_LAYER_START_USD",
        "INCOME_LAYER_MAX_RATIO",
        "DCA_MODE",
        "DCA_BASE_INVESTMENT_USD",
        "IBIT_ZSCORE_EXIT_ENABLED",
        "IBIT_ZSCORE_EXIT_MODE",
        "IBIT_ZSCORE_EXIT_PARKING_SYMBOL",
        "IBIT_ZSCORE_EXIT_RISK_REDUCED_EXPOSURE",
        "IBIT_ZSCORE_EXIT_RISK_OFF_EXPOSURE",
        "IBIT_ZSCORE_EXIT_ALLOW_OUTSIDE_EXECUTION_WINDOW",
        "FIRSTRADE_MARKET_SIGNAL_HANDOFF_INDEX_URI",
        "FIRSTRADE_MARKET_SIGNAL_HANDOFF_MANIFEST_URI",
        "FIRSTRADE_MARKET_SIGNAL_CONSUMPTION_AUDIT_URI",
        "FIRSTRADE_MARKET_SIGNAL_CACHE_DIR",
        "FIRSTRADE_MARKET_SIGNAL_REQUIRED",
        "FIRSTRADE_MARKET_SIGNAL_FALLBACK_MODE",
        "FIRSTRADE_MARKET_SIGNAL_MAX_STALE_DAYS",
        "FIRSTRADE_FEATURE_SNAPSHOT_FALLBACK_MODE",
        "FIRSTRADE_FEATURE_SNAPSHOT_FALLBACK_CACHE_DIR",
        "FIRSTRADE_FEATURE_SNAPSHOT_MAX_STALE_DAYS",
    ):
        assert f"{name}: ${{{{ vars.{name} }}}}" in workflow

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
        "${{ secrets.TG_TOKEN }}"
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
    assert 'env_pairs+=("GOOGLE_CLOUD_PROJECT=${GCP_PROJECT_ID}")' in workflow
    assert "for key, value in sorted(target[\"env\"].items()):" in workflow
    assert "target.get(\"remove_env_vars\")" in workflow

    assert "Reconcile Cloud Run traffic" in workflow
    assert "python scripts/reconcile_cloud_runtime.py traffic" in workflow
    assert "Reconcile legacy Cloud Scheduler jobs" in workflow
    assert "python scripts/reconcile_cloud_runtime.py scheduler-cleanup" in workflow
    assert "add_optional_env " not in workflow
    assert "requires_snapshot_artifacts=" not in workflow
    assert "Resolve selected strategy runtime requirements" not in workflow
    assert "Cloud Run env sync currently supports exactly one target" not in workflow
    assert workflow.count("matching_targets = [") >= 5
    assert workflow.count("if len(matching_targets) != 1:") >= 5
    assert workflow.index("name: Validate env sync inputs") < workflow.index(
        "name: Sync Cloud Run environment"
    )


def test_sync_cloud_run_env_workflow_syncs_scheduler_from_sync_plan():
    workflow_path = Path(__file__).resolve().parents[1] / ".github/workflows/sync-cloud-run-env.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "Sync Cloud Scheduler schedule" in workflow
    assert "GCP_SCHEDULER_SERVICE_ACCOUNT: firstrade-platform-scheduler@firstradequant.iam.gserviceaccount.com" in workflow
    assert "MONITOR_DISPATCH_TARGETS_JSON=${monitor_targets_json}" in workflow
    assert 'scheduler_location="${CLOUD_SCHEDULER_LOCATION:-${CLOUD_RUN_REGION}}"' in workflow
    assert 'plan = json.loads(os.environ["SYNC_PLAN_JSON"])' in workflow
    assert (
        'str(candidate.get("service_name") or "").strip() == os.environ["CLOUD_RUN_SERVICE"]'
        in workflow
    )
    assert 'scheduler = target.get("scheduler") or {}' in workflow
    assert 'print(str(scheduler.get("timezone") or "America/New_York").strip())' in workflow
    assert 'scheduler.get("main_time") or os.environ.get("CLOUD_SCHEDULER_MAIN_TIME"' in workflow
    assert 'scheduler.get("probe_time") or os.environ.get("CLOUD_SCHEDULER_PROBE_TIME"' in workflow
    assert 'scheduler.get("precheck_time") or os.environ.get("CLOUD_SCHEDULER_PRECHECK_TIME"' in workflow
    assert 'scheduler_job_candidates=("${CLOUD_RUN_SERVICE}-scheduler")' in workflow
    assert 'scheduler_job_candidates+=("${CLOUD_RUN_SERVICE%-service}-scheduler")' in workflow
    assert 'if len(time_fields) == 5:' in workflow
    assert 'print(" ".join(time_fields))' in workflow
    assert 'print(" ".join([*time_fields, *current_fields[2:]]))' in workflow
    assert 'gcloud scheduler jobs update http "${job_name}"' in workflow
    assert 'gcloud scheduler jobs create http "${job_name}"' in workflow
    assert 'runtime_target_enabled="${scheduler_config[4]}"' in workflow
    assert 'desired_probe_schedule="$(CURRENT_SCHEDULE="${desired_schedule}" SCHEDULE_TIME="${probe_time}" python - <<' in workflow
    assert 'desired_precheck_schedule="$(CURRENT_SCHEDULE="${desired_schedule}" SCHEDULE_TIME="${precheck_time}" python - <<' in workflow
    assert 'probe_job_name="${CLOUD_RUN_SERVICE}-probe-scheduler"' in workflow
    assert 'probe_uri="${service_url}/probe"' in workflow
    assert 'precheck_job_name="${CLOUD_RUN_SERVICE}-precheck-scheduler"' in workflow
    assert 'precheck_uri="${service_url}/dry-run"' in workflow
    assert 'managed_scheduler_jobs=("${job_name}")' in workflow
    assert 'if [ "${DIRECT_MONITOR_MIGRATION_COMPLETE:-}" = "true" ]; then' in workflow
    assert 'managed_scheduler_jobs+=("${probe_job_name}" "${precheck_job_name}")' in workflow
    assert "id: scheduler_sync" in workflow
    assert 'monitor_job_name="firstrade-monitor-dispatcher-scheduler"' in workflow
    assert 'monitor_uri="${service_url}/monitor-dispatch"' in workflow
    assert '--schedule="*/5 * * * *"' in workflow
    assert "invoke_bridge_jobs=(" in workflow
    assert '--schedule="0 0 1 1 *"' in workflow
    assert 'echo "direct_monitors_reconciled=true" >> "${GITHUB_OUTPUT}"' in workflow
    assert (
        "DIRECT_MONITOR_SCHEDULERS_RECONCILED: "
        "${{ steps.scheduler_sync.outputs.direct_monitors_reconciled }}"
    ) in workflow
    assert 'gcloud scheduler jobs resume "${managed_job_name}"' in workflow
    assert 'gcloud scheduler jobs pause "${managed_job_name}"' in workflow
    assert (
        "DIRECT_MONITOR_MIGRATION_COMPLETE: "
        "${{ vars.DIRECT_MONITOR_MIGRATION_COMPLETE }}"
    ) in workflow
    assert 'DIRECT_MONITOR_MIGRATION_COMPLETE: "true"' not in workflow
    assert '--schedule="${desired_schedule}"' in workflow
    assert '--time-zone="${market_timezone}"' in workflow
    assert "legacy_jobs=(" not in workflow
    direct_gate = workflow.index(
        'if [ "${DIRECT_MONITOR_MIGRATION_COMPLETE:-}" = "true" ]; then'
    )
    assert direct_gate < workflow.index(
        'desired_probe_schedule="$(CURRENT_SCHEDULE="${desired_schedule}"'
    )
    assert direct_gate < workflow.index(
        'desired_precheck_schedule="$(CURRENT_SCHEDULE="${desired_schedule}"'
    )


def test_sync_cloud_run_env_workflow_hardens_deploy_runtime_boundary():
    workflow_path = Path(__file__).resolve().parents[1] / ".github/workflows/sync-cloud-run-env.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    deploy_block = workflow[
        workflow.index('gcloud run deploy "${CLOUD_RUN_SERVICE}"') : workflow.index(
            "      - name: Check whether env sync is enabled"
        )
    ]

    assert "--no-allow-unauthenticated" in deploy_block
    assert "--allow-unauthenticated" not in deploy_block
    assert "--ingress=internal" in deploy_block
    assert "--max-instances=1" in deploy_block
    assert "--concurrency=1" in deploy_block
    assert "--concurrency=80" not in deploy_block


def test_main_scheduler_update_and_create_are_authenticated_post_requests():
    workflow_path = Path(__file__).resolve().parents[1] / ".github/workflows/sync-cloud-run-env.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    scheduler_block = workflow[
        workflow.index('scheduler_uri="${service_url}/run"') : workflow.index(
            'managed_scheduler_jobs=("${job_name}")'
        )
    ]

    assert scheduler_block.count('gcloud scheduler jobs update http "${job_name}"') == 1
    assert scheduler_block.count('gcloud scheduler jobs create http "${job_name}"') == 1
    assert scheduler_block.count("--http-method=POST") == 2
    assert scheduler_block.count(
        '--oidc-service-account-email="${GCP_SCHEDULER_SERVICE_ACCOUNT}"'
    ) == 2
    assert scheduler_block.count('--oidc-token-audience="${service_url}"') == 2
