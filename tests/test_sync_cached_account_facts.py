import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest


spec = importlib.util.spec_from_file_location(
    "cached_snapshot", Path(__file__).parents[1] / "scripts/sync_cached_account_facts.py"
)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
URL = "https://synthetic.example"
JOB = "projects/synthetic/locations/test/jobs/synthetic-account-facts-sync-scheduler"


def bridge():
    return {"name": JOB, "state": "ENABLED", "httpTarget": {
        "uri": URL + "/account-facts-sync", "httpMethod": "POST",
        "oidcToken": {"audience": URL, "serviceAccountEmail": "synthetic@example.invalid"},
    }}


def completion():
    return {"jsonPayload": {
        "@type": "type.googleapis.com/google.cloud.scheduler.logging.AttemptFinished",
        "jobName": JOB, "url": URL + "/account-facts-sync",
        "debugInfo": "URL_CRAWLED. Original HTTP response code number = 200",
    }}


@pytest.mark.parametrize("field,value", [
    ("uri", URL + "/run"), ("uri", URL + "/session-renew"),
    ("httpMethod", "GET"), ("body", "synthetic"),
    ("oidcToken", {"audience": URL + "/other", "serviceAccountEmail": "synthetic"}),
])
def test_rejects_any_non_snapshot_bridge(field, value):
    job = bridge()
    job["httpTarget"][field] = value
    with pytest.raises(sync.SnapshotSyncError, match="snapshot_bridge_mismatch"):
        sync.validate_bridge(job, URL)


def test_rejects_disabled_or_retrying_bridge():
    for changed in ({"state": "PAUSED"}, {"retryConfig": {"retryCount": 1}}):
        job = bridge() | changed
        with pytest.raises(sync.SnapshotSyncError):
            sync.validate_bridge(job, URL)


def test_dispatch_or_http_completion_alone_is_not_publication():
    sync.validate_bridge(bridge(), URL)
    assert not sync.completed_snapshot([], JOB, URL)
    assert not sync.completed_snapshot([completion()], JOB, URL)
    ack = {"textPayload": "INFO account_facts_sync published ok"}
    assert not sync.completed_snapshot([ack], JOB, URL)
    assert sync.completed_snapshot([completion(), ack], JOB, URL)
    wrong = completion()
    wrong["jsonPayload"]["jobName"] += "-other"
    assert not sync.completed_snapshot([wrong, ack], JOB, URL)


def test_auth_or_provider_failure_is_not_empty_success():
    failed = completion()
    failed["jsonPayload"].update(status="UNAVAILABLE", debugInfo="HTTP 503")
    with pytest.raises(sync.SnapshotSyncError):
        sync.completed_snapshot([failed, {"textPayload": "account_facts_sync published ok"}], JOB, URL)


def test_main_runs_bridge_once_and_keeps_provider_output_private(monkeypatch, capsys):
    for key, value in {"GCP_PROJECT_ID": "synthetic", "CLOUD_RUN_REGION": "test",
                       "CLOUD_RUN_SERVICE": "synthetic"}.items():
        monkeypatch.setenv(key, value)
    run = Mock(side_effect=[
        {"status": {"url": URL}}, bridge(), None,
        [completion(), {"textPayload": "account_facts_sync published ok private-body"}],
    ])
    monkeypatch.setattr(sync, "cloud", run)
    assert sync.main() == 0
    assert sum(call.args[:3] == ("scheduler", "jobs", "run") for call in run.call_args_list) == 1
    output = capsys.readouterr().out
    assert "private-body" not in output and URL not in output and JOB not in output
    assert json.loads(output.splitlines()[-1])["status"] == "snapshot_published_verified"


def test_unknown_dispatch_outcome_never_retries(monkeypatch, capsys):
    for key in ("GCP_PROJECT_ID", "CLOUD_RUN_REGION", "CLOUD_RUN_SERVICE"):
        monkeypatch.setenv(key, "synthetic")
    run = Mock(side_effect=[{"status": {"url": URL}}, bridge(),
                           sync.SnapshotSyncError("cloud_operation_failed_no_retry")])
    monkeypatch.setattr(sync, "cloud", run)
    assert sync.main() == 1
    assert run.call_count == 3
    assert json.loads(capsys.readouterr().out)["retry"] is False
