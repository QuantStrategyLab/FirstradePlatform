from __future__ import annotations

import subprocess
import datetime as dt
import json

from scripts import execution_report_heartbeat as heartbeat


def test_required_services_skip_disabled_runtime_targets(monkeypatch):
    for name in (
        "RUNTIME_HEARTBEAT_REQUIRED_SERVICES",
        "CLOUD_RUN_SERVICES",
        "CLOUD_RUN_SERVICE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(
        "CLOUD_RUN_SERVICE_TARGETS_JSON",
        json.dumps(
            {
                "defaults": {"RUNTIME_TARGET_ENABLED": "false"},
                "targets": [
                    {"service": "firstrade-enabled-service", "RUNTIME_TARGET_ENABLED": "true"},
                    {"service": "firstrade-disabled-service"},
                ]
            }
        ),
    )

    assert heartbeat._load_required_services() == ["firstrade-enabled-service"]


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


def test_execution_report_uri_disables_strategy_state_fallback(monkeypatch):
    monkeypatch.delenv("RUNTIME_HEARTBEAT_GCS_URIS", raising=False)
    monkeypatch.setenv(
        "EXECUTION_REPORT_GCS_URI",
        "gs://runtime-reports/execution-reports",
    )
    monkeypatch.setenv("FIRSTRADE_GCS_STATE_BUCKET", "runtime-state")
    monkeypatch.setenv("FIRSTRADE_STATE_PREFIX", "firstrade-platform")

    assert heartbeat._base_report_uris() == [
        "gs://runtime-reports/execution-reports",
    ]


def test_telegram_token_falls_back_to_secret_manager(monkeypatch):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TG_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_TOKEN_SECRET_NAME", "platform-telegram-token")
    monkeypatch.setenv("GCP_PROJECT_ID", "firstradequant")
    observed = {}

    def fake_run_gcloud(command):
        observed["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout="secret-token\n", stderr="")

    monkeypatch.setattr(heartbeat, "_run_gcloud", fake_run_gcloud)

    assert heartbeat._telegram_token() == "secret-token"
    assert observed["command"] == [
        "gcloud",
        "secrets",
        "versions",
        "access",
        "latest",
        "--secret",
        "platform-telegram-token",
        "--project",
        "firstradequant",
    ]


def test_heartbeat_skips_when_runtime_target_is_disabled(monkeypatch, capsys):
    monkeypatch.setenv("RUNTIME_HEARTBEAT_NAME", "Firstrade disabled runtime")
    monkeypatch.setenv("RUNTIME_TARGET_ENABLED", "false")
    monkeypatch.setattr(
        heartbeat,
        "_list_gcs_objects",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("GCS should not be queried")),
    )

    result = heartbeat.main(now=dt.datetime(2026, 6, 20, 23, 10, tzinfo=dt.timezone.utc))

    assert result == 0
    output = capsys.readouterr().out
    assert "Execution report heartbeat skipped for Firstrade disabled runtime" in output
    assert "runtime target is disabled" in output


def test_heartbeat_skips_when_runtime_target_json_is_disabled(monkeypatch, capsys):
    monkeypatch.delenv("RUNTIME_TARGET_ENABLED", raising=False)
    monkeypatch.setenv("RUNTIME_HEARTBEAT_NAME", "Firstrade disabled runtime")
    monkeypatch.setenv("RUNTIME_TARGET_JSON", '{"runtime_target_enabled":false}')
    monkeypatch.setattr(
        heartbeat,
        "_list_gcs_objects",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("GCS should not be queried")),
    )

    result = heartbeat.main(now=dt.datetime(2026, 6, 20, 23, 10, tzinfo=dt.timezone.utc))

    assert result == 0
    output = capsys.readouterr().out
    assert "Execution report heartbeat skipped for Firstrade disabled runtime" in output
    assert "runtime target is disabled" in output


def test_heartbeat_skips_when_all_configured_targets_are_disabled(
    monkeypatch,
    capsys,
):
    monkeypatch.delenv("RUNTIME_TARGET_ENABLED", raising=False)
    monkeypatch.delenv("RUNTIME_TARGET_JSON", raising=False)
    monkeypatch.setenv("RUNTIME_HEARTBEAT_NAME", "Firstrade disabled targets")
    monkeypatch.setenv(
        "CLOUD_RUN_SERVICE_TARGETS_JSON",
        json.dumps(
            {
                "defaults": {"runtime_target_enabled": False},
                "targets": [{"service": "disabled-service"}],
            }
        ),
    )
    monkeypatch.setattr(
        heartbeat,
        "_list_gcs_objects",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("GCS should not be queried")
        ),
    )

    result = heartbeat.main(
        now=dt.datetime(2026, 6, 20, 23, 10, tzinfo=dt.timezone.utc)
    )

    assert result == 0
    assert "no enabled runtime target matches this heartbeat" in capsys.readouterr().out


def test_incomplete_target_schedule_uses_deployed_scheduler_cron(monkeypatch):
    targets = [
        {
            "service": "firstrade-service",
            "scheduler": {
                "main_time": "45 15",
                "timezone": "America/New_York",
            },
        }
    ]
    monkeypatch.setattr(
        heartbeat,
        "_describe_scheduler_job",
        lambda job_name, **_kwargs: (
            {
                "name": job_name,
                "schedule": "45 15 25-29 * *",
                "timeZone": "America/New_York",
            }
            if job_name == "firstrade-service-scheduler"
            else None
        ),
    )

    hydrated = heartbeat._hydrate_runtime_target_schedules(
        targets,
        project="test-project",
    )

    assert hydrated[0]["scheduler"]["main_time"] == "45 15 25-29 * *"



def test_heartbeat_skips_outside_runtime_target_scheduler_day(monkeypatch, capsys):
    monkeypatch.setenv("RUNTIME_HEARTBEAT_NAME", "Firstrade monthly runtime")
    monkeypatch.setenv(
        "RUNTIME_TARGET_JSON",
        '{"scheduler":{"timezone":"America/New_York","main_time":"45 15 25-28 * *"}}',
    )
    monkeypatch.setattr(
        heartbeat,
        "_list_gcs_objects",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("GCS should not be queried")),
    )

    result = heartbeat.main(now=dt.datetime(2026, 6, 20, 23, 10, tzinfo=dt.timezone.utc))

    assert result == 0
    output = capsys.readouterr().out
    assert "Execution report heartbeat skipped for Firstrade monthly runtime" in output
    assert "expected day(s)=25,26,27,28" in output


def test_heartbeat_does_not_skip_inside_runtime_target_scheduler_day(monkeypatch):
    monkeypatch.setenv(
        "RUNTIME_TARGET_JSON",
        '{"scheduler":{"timezone":"America/New_York","main_time":"45 15 25-28 * *"}}',
    )
    now = dt.datetime(2026, 6, 25, 23, 10, tzinfo=dt.timezone.utc)

    reason = heartbeat._heartbeat_skip_reason_for_schedule(
        now - dt.timedelta(hours=36),
        now,
    )

    assert reason is None


def test_heartbeat_does_not_skip_when_lookback_includes_scheduler_day(monkeypatch):
    monkeypatch.setenv(
        "RUNTIME_TARGET_JSON",
        '{"scheduler":{"timezone":"America/New_York","main_time":"45 15 25-28 * *"}}',
    )

    reason = heartbeat._heartbeat_skip_reason_for_schedule(
        dt.datetime(2026, 6, 28, 20, 0, tzinfo=dt.timezone.utc),
        dt.datetime(2026, 6, 29, 20, 0, tzinfo=dt.timezone.utc),
    )

    assert reason is None


def test_report_with_failed_notification_delivery_is_rejected():
    accepted, reason = heartbeat._is_accepted_report(
        {
            "status": "ok",
            "summary": {
                "notification_sent": False,
                "notification_suppressed": False,
                "notification_error": "delivery_not_acknowledged",
            },
        }
    )

    assert accepted is False
    assert "notification delivery failed" in reason


def _synthetic_schedule_environment(monkeypatch, *, timezone="America/New_York", full_target=False):
    import os
    for name in list(os.environ):
        if name.startswith(("RUNTIME_HEARTBEAT_", "CLOUD_RUN_", "FIRSTRADE_")) or name in {
            "RUNTIME_TARGET_JSON", "RUNTIME_TARGET_ENABLED", "CLOUD_SCHEDULER_MAIN_TIME", "EXECUTION_REPORT_GCS_URI",
        }:
            monkeypatch.delenv(name, raising=False)
    target = {"scheduler": {"timezone": timezone, "main_time": "0 10 25-29 * 1-5"}}
    if full_target:
        target.update(service_name="synthetic-service", strategy_profile="synthetic_profile", account_scope="synthetic_scope", market="US", market_calendar="NYSE", market_timezone="America/New_York")
    monkeypatch.setenv("RUNTIME_TARGET_JSON", json.dumps(target))
    monkeypatch.setenv("RUNTIME_HEARTBEAT_GCS_URIS", "gs://synthetic/offline-reports")
    monkeypatch.setenv("RUNTIME_HEARTBEAT_NOTIFY_ON_SUCCESS", "false")
    monkeypatch.setenv("RUNTIME_HEARTBEAT_PUBLICATION_GRACE_MINUTES", "30")
    monkeypatch.setenv("RUNTIME_HEARTBEAT_FAIL_WORKFLOW_ON_ALERT", "true")
    return target


def test_dom_and_dow_or_match_cannot_be_labeled_dom_not_due(monkeypatch):
    from quant_platform_kit.common.runtime_heartbeat_policy import cron_matches
    _synthetic_schedule_environment(monkeypatch)
    now = dt.datetime(2026, 10, 5, 14, 31, tzinfo=dt.timezone.utc)
    local_due = dt.datetime(2026, 10, 5, 10, 0, tzinfo=heartbeat.ZoneInfo("America/New_York"))
    assert cron_matches("0 10 25-29 * 1-5", local_due) is True
    assert heartbeat._heartbeat_skip_reason_for_schedule(now - dt.timedelta(hours=24), now) is None


def test_invalid_timezone_cannot_support_dom_not_due_inference(monkeypatch):
    _synthetic_schedule_environment(monkeypatch, timezone="Invalid/Offline")
    monkeypatch.setenv("RUNTIME_TARGET_JSON", json.dumps({"scheduler": {"timezone": "Invalid/Offline", "main_time": "0 10 25-29 * *"}}))
    now = dt.datetime(2026, 10, 5, 14, 31, tzinfo=dt.timezone.utc)
    assert heartbeat._heartbeat_skip_reason_for_schedule(now - dt.timedelta(hours=24), now) is None


def _mock_offline_archive_and_notifier(monkeypatch):
    observed = {"archive_reads": 0, "messages": []}
    monkeypatch.setattr(heartbeat, "_hydrate_runtime_target_schedules", lambda targets, **_kwargs: targets)
    def archive(*_args, **_kwargs):
        observed["archive_reads"] += 1
        return []
    monkeypatch.setattr(heartbeat, "_list_gcs_objects", archive)
    monkeypatch.setattr(heartbeat, "_send_telegram", lambda message, **_kwargs: observed["messages"].append(message) or True)
    return observed


def test_main_fallback_does_not_quiet_a_weekday_or_due_missing_report(monkeypatch):
    _synthetic_schedule_environment(monkeypatch)
    assert heartbeat.load_runtime_targets(__import__("os").environ) == []
    observed = _mock_offline_archive_and_notifier(monkeypatch)
    now = dt.datetime(2026, 10, 5, 14, 31, tzinfo=dt.timezone.utc)
    assert heartbeat.main(now=now) == 1
    assert observed["archive_reads"] > 0
    assert len(observed["messages"]) == 1


def test_main_fallback_invalid_timezone_does_not_quiet_missing_evidence(monkeypatch):
    _synthetic_schedule_environment(monkeypatch, timezone="Invalid/Offline")
    monkeypatch.setenv("RUNTIME_TARGET_JSON", json.dumps({"scheduler": {"timezone": "Invalid/Offline", "main_time": "0 10 25-29 * *"}}))
    observed = _mock_offline_archive_and_notifier(monkeypatch)
    now = dt.datetime(2026, 10, 5, 14, 31, tzinfo=dt.timezone.utc)
    assert heartbeat.main(now=now) == 1
    assert observed["archive_reads"] > 0
    assert len(observed["messages"]) == 1


def test_full_runtime_targets_due_still_use_shared_market_and_grace_contract(monkeypatch):
    _synthetic_schedule_environment(monkeypatch, full_target=True)
    observed = _mock_offline_archive_and_notifier(monkeypatch)
    original_filter = heartbeat.filter_due_targets
    def filter_with_mock_calendar(targets, **kwargs):
        return original_filter(targets, **kwargs, session_dates_loader=lambda *_args, **_kwargs: {dt.date(2026, 10, 5)})
    monkeypatch.setattr(heartbeat, "filter_due_targets", filter_with_mock_calendar)
    monkeypatch.setattr(heartbeat, "_heartbeat_skip_reason_for_schedule", lambda *_args: (_ for _ in ()).throw(AssertionError("DOM-only fallback must not override a due full target")))
    now = dt.datetime(2026, 10, 5, 14, 31, tzinfo=dt.timezone.utc)
    assert heartbeat.main(now=now) == 1
    assert observed["archive_reads"] > 0
    assert len(observed["messages"]) == 1
