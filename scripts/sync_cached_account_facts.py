#!/usr/bin/env python3
"""Invoke the existing cached-only snapshot bridge once; print safe states only."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import time


class SnapshotSyncError(RuntimeError):
    """Fixed reason codes; provider output must never enter logs."""


def cloud(*args):
    account = os.environ.get("GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT")
    if not account:
        raise SnapshotSyncError("snapshot_reader_account_missing")
    result = subprocess.run(
        ["gcloud", *args, "--account=" + account, "--project", os.environ["GCP_PROJECT_ID"], "--format=json", "--quiet"],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if result.returncode:
        raise SnapshotSyncError("cloud_operation_failed_no_retry")
    try:
        return json.loads(result.stdout) if result.stdout.strip() else None
    except ValueError:
        raise SnapshotSyncError("cloud_response_invalid") from None


def validate_bridge(job, service_url):
    target = job.get("httpTarget") or {}
    oidc = target.get("oidcToken") or {}
    if not (
        job.get("state") == "ENABLED"
        and target.get("uri") == service_url + "/account-facts-sync"
        and target.get("httpMethod") == "POST"
        and not target.get("body")
        and oidc.get("audience") == service_url
        and oidc.get("serviceAccountEmail")
        and job.get("retryConfig", {}).get("retryCount", 0) == 0
    ):
        raise SnapshotSyncError("snapshot_bridge_mismatch")


def completed_snapshot(rows, job_name, service_url):
    """Require both Scheduler completion and the exact application HTTP 200."""
    finished = []
    app_completed = False
    failures = set()
    for row in rows:
        payload = row.get("jsonPayload") or {}
        if (
            payload.get("@type") == "type.googleapis.com/google.cloud.scheduler.logging.AttemptFinished"
            and payload.get("jobName") == job_name
            and payload.get("url") == service_url + "/account-facts-sync"
        ):
            finished.append(payload)
        http = row.get("httpRequest") or {}
        if (
            row.get("resource", {}).get("type") == "cloud_run_revision"
            and http.get("requestUrl") == service_url + "/account-facts-sync"
        ):
            status = http.get("status")
            if status == 200:
                app_completed = True
            elif type(status) is int and status in {401, 403, 429, 500, 502, 503, 504}:
                failures.add(status)
    if failures:
        # Preserve the observed HTTP class without exposing provider bodies or
        # guessing that an unavailable service is an expired broker session.
        if len(failures) == 1:
            raise SnapshotSyncError("snapshot_request_http_" + str(next(iter(failures))))
        raise SnapshotSyncError("snapshot_request_multiple_http_failures")
    if any(
        event.get("status") not in (None, "OK")
        or "response code number = 200" not in event.get("debugInfo", "")
        for event in finished
    ):
        raise SnapshotSyncError("snapshot_request_failed_auth_or_provider_unavailable")
    return bool(finished) and app_completed


def main():
    try:
        project = os.environ["GCP_PROJECT_ID"]
        region = os.environ["CLOUD_RUN_REGION"]
        service = os.environ["CLOUD_RUN_SERVICE"]
        if not all((project, region, service)):
            raise SnapshotSyncError("snapshot_configuration_missing")
        metadata = cloud("run", "services", "describe", service, "--region", region)
        service_url = metadata["status"]["url"]
        job = cloud(
            "scheduler", "jobs", "describe", service + "-account-facts-sync-scheduler",
            "--location", region,
        )
        validate_bridge(job, service_url)
        since = dt.datetime.now(dt.timezone.utc).isoformat()
        # This is the only mutation. Unknown outcomes require readback, never another run.
        cloud("scheduler", "jobs", "run", job["name"], "--location", region)
        print(json.dumps({"status": "snapshot_dispatched", "started_at": since}), flush=True)
        log_filter = (
            f'timestamp>="{since}" AND ('
            f'(resource.type="cloud_scheduler_job" AND resource.labels.job_id="{job["name"].split("/")[-1]}" '
            'AND jsonPayload.@type="type.googleapis.com/google.cloud.scheduler.logging.AttemptFinished") OR '
            f'(resource.type="cloud_run_revision" AND resource.labels.service_name="{service}" '
            f'AND httpRequest.requestUrl="{service_url}/account-facts-sync"))'
        )
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            rows = cloud("logging", "read", log_filter, "--limit=20")
            if completed_snapshot(rows, job["name"], service_url):
                print(json.dumps({"status": "snapshot_request_completed", "http_status": 200,
                                  "scheduler_config_writes": 0, "session_renew_calls": 0}))
                return 0
            time.sleep(5)
        raise SnapshotSyncError("snapshot_completion_unverified_no_retry")
    except (SnapshotSyncError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        reason = str(exc) if isinstance(exc, SnapshotSyncError) else "snapshot_observation_unavailable"
        print(json.dumps({"status": "snapshot_failed", "reason": reason, "retry": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
