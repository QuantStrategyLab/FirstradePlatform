from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import diagnose_cached_stage_failure as diagnostic


PRIVATE_SERVICE = "private-service-placeholder"
PRIVATE_NAME = "private-revision-placeholder"
PRIVATE_MESSAGE = "secret placeholder: failed to pull image for private-service-placeholder"
PRIVATE_PROJECT = "private-project-placeholder"
PRIVATE_REGION = "private-region-placeholder"
WINDOW_START = "2026-10-02T00:00:00Z"
WINDOW_END = "2026-10-02T00:10:00Z"


def _evidence(message: str = PRIVATE_MESSAGE):
    service = {
        "metadata": {"name": PRIVATE_SERVICE},
        "status": {
            "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}],
            "latestCreatedRevisionName": PRIVATE_NAME,
            "latestReadyRevisionName": PRIVATE_NAME,
            "traffic": [{"revisionName": PRIVATE_NAME, "percent": 100}],
        },
    }
    revisions = [
        {
            "metadata": {"name": PRIVATE_NAME},
            "status": {
                "conditions": [
                    {"type": "Ready", "state": "CONDITION_FAILED", "reason": "ContainerFailure", "message": message}
                ]
            },
            "spec": {"containers": [{"env": [{"name": "PRIVATE_KEY", "value": "private-value-placeholder"}]}]},
        }
    ]
    policy = {
        "bindings": [
            {"role": "roles/run.invoker", "members": ["serviceAccount:private@example.invalid"]}
        ]
    }
    jobs = [{"name": "private-job-placeholder", "state": "PAUSED"}]
    audit = [
        {
            "timestamp": "2026-10-02T00:05:00Z",
            "resource": {
                "labels": {
                    "project_id": PRIVATE_PROJECT,
                    "location": PRIVATE_REGION,
                    "service_name": PRIVATE_SERVICE,
                }
            },
            "protoPayload": {
                "methodName": "google.cloud.run.v2.Services.UpdateService",
                "resourceName": f"projects/{PRIVATE_PROJECT}/locations/{PRIVATE_REGION}/services/{PRIVATE_SERVICE}",
                "request": {"env": "private-value-placeholder"},
                "status": {"code": 13, "message": message},
            },
        }
    ]
    return service, revisions, policy, jobs, audit


def test_summary_exposes_only_closed_statuses_counts_and_categories():
    summary = diagnostic.summarize(
        service=_evidence()[0],
        revisions=_evidence()[1],
        expected_service=PRIVATE_SERVICE,
        expected_project=PRIVATE_PROJECT,
        expected_region=PRIVATE_REGION,
        audit_window_start=WINDOW_START,
        audit_window_end=WINDOW_END,
        policy=_evidence()[2],
        jobs=_evidence()[3],
        audit_entries=_evidence()[4],
        audit_status="ok",
    )

    assert summary == {
        "schema_version": "firstrade_cached_stage_diagnostic.v1",
        "service_readable": True,
        "target_matches": True,
        "diagnostic_tag_budget_ok": True,
        "service_name_length": len(PRIVATE_SERVICE),
        "failure_subcategory": "none",
        "combined_traffic_tag_service_name_length_error_observed": False,
        "service_ready": True,
        "traffic_row_count": 1,
        "positive_traffic_row_count": 1,
        "traffic_is_single_ready_revision_at_100_percent": True,
        "latest_created_revision_found": True,
        "revision_list_readable": True,
        "latest_created_revision_ready": False,
        "latest_revision_error_categories": ["image"],
        "latest_revision_error_status_codes": [],
        "audit_error_message_terms": {
            "container": False,
            "name": False,
            "image": True,
            "traffic": False,
            "tag": False,
            "revision": False,
            "env": False,
            "secret": True,
            "update_mask": False,
            "port": False,
            "vpc": False,
            "quota": False,
            "immutable": False,
        },
        "iam_policy_readable": True,
        "iam_binding_count": 1,
        "scheduler_readable": True,
        "scheduler_job_count": 1,
        "scheduler_state_counts": {"enabled": 0, "paused": 1, "other": 0},
        "audit_query_status": "ok",
        "audit_entry_count": 1,
        "audit_error_count": 1,
        "audit_error_categories": ["image"],
        "audit_error_status_codes": [13],
        "failure_source": "audit",
        "failure_categories": ["image"],
    }
    serialized = json.dumps(summary)
    for private_value in (PRIVATE_SERVICE, PRIVATE_NAME, PRIVATE_MESSAGE, "private-value-placeholder", "private@example.invalid"):
        assert private_value not in serialized


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("permission denied: PERMISSION_DENIED", ["permission"]),
        ("iam.serviceAccounts.actAs is required", ["act_as"]),
        ("revision name is invalid", ["invalid_name"]),
        ("failed to pull image manifest", ["image"]),
        ("container failed to listen on the port", ["port"]),
        ("startup probe failed", ["startup"]),
        ("secret version was not found", ["missing_secret"]),
        ("updateMask contains a private field", ["update_mask"]),
        ("environment variable is invalid", ["environment"]),
        ("VPC connector could not be resolved", ["network"]),
        ("RESOURCE_EXHAUSTED: quota exceeded", ["quota"]),
        (
            "traffic[].tag: traffic tag [TAG] and service name [SERVICE] together are too long. "
            "Combined traffic tag and service name cannot exceed 46 characters.",
            ["invalid_name", "traffic_tag_length"],
        ),
        ("opaque upstream diagnostic", ["unknown"]),
    ],
)
def test_error_text_is_reduced_to_fixed_reason(message, expected):
    assert diagnostic._classify([message]) == expected


@pytest.mark.parametrize(("message", "reason"), [
    ("Traffic tag [PRIVATE_TAG] should be at most one character long", "traffic_tag_length"),
    ("Traffic tag [PRIVATE_TAG] must have a valid DNS format", "traffic_tag_format"),
    ("Traffic tag [PRIVATE_TAG] is reserved", "traffic_tag_conflict"),
    ("Traffic tags are not supported when the URL is disabled", "traffic_tag_url_disabled"),
])
def test_traffic_tag_rejections_remain_closed(message, reason):
    summary = diagnostic._classify([message])
    assert summary == [reason]
    assert "PRIVATE_TAG" not in json.dumps(summary)


def test_missing_logging_permission_is_reported_without_guessing_failure_category():
    service, revisions, policy, jobs, audit = _evidence("permission denied")
    summary = diagnostic.summarize(
        service=service,
        revisions=revisions,
        expected_service=PRIVATE_SERVICE,
        expected_project=PRIVATE_PROJECT,
        expected_region=PRIVATE_REGION,
        audit_window_start=WINDOW_START,
        audit_window_end=WINDOW_END,
        policy=policy,
        jobs=jobs,
        audit_entries=audit,
        audit_status="permission_denied",
    )
    assert summary["audit_query_status"] == "permission_denied"
    assert summary["audit_entry_count"] is None
    assert summary["failure_source"] == "revision"
    assert summary["failure_categories"] == ["permission"]


def test_combined_traffic_tag_length_is_closed_subcategory_and_budget_stays_private():
    message = (
        "traffic[].tag: traffic tag [TAG] and service name [SERVICE] together are too long. "
        "Combined traffic tag and service name cannot exceed 46 characters."
    )
    service, revisions, policy, jobs, audit = _evidence(message)
    summary = diagnostic.summarize(
        service=service,
        revisions=revisions,
        expected_service=PRIVATE_SERVICE,
        expected_project=PRIVATE_PROJECT,
        expected_region=PRIVATE_REGION,
        audit_window_start=WINDOW_START,
        audit_window_end=WINDOW_END,
        policy=policy,
        jobs=jobs,
        audit_entries=audit,
        audit_status="ok",
    )
    assert summary["failure_categories"] == ["invalid_name", "traffic_tag_length"]
    assert summary["failure_subcategory"] == "combined_traffic_tag_service_name_length"
    assert summary["combined_traffic_tag_service_name_length_error_observed"] is True
    assert summary["diagnostic_tag_budget_ok"] is True
    assert PRIVATE_SERVICE not in json.dumps(summary)


def test_status_codes_and_message_terms_are_numeric_and_closed():
    message = (
        "permission denied: updateMask for VPC connector private-vpc "
        "failed on immutable revision private-revision-placeholder"
    )
    service, revisions, policy, jobs, audit = _evidence(message)
    revisions[0]["status"]["conditions"][0]["code"] = 14
    audit[0]["protoPayload"]["status"]["code"] = 7
    summary = diagnostic.summarize(
        service=service,
        revisions=revisions,
        expected_service=PRIVATE_SERVICE,
        expected_project=PRIVATE_PROJECT,
        expected_region=PRIVATE_REGION,
        audit_window_start=WINDOW_START,
        audit_window_end=WINDOW_END,
        policy=policy,
        jobs=jobs,
        audit_entries=audit,
        audit_status="ok",
    )
    assert summary["latest_revision_error_status_codes"] == [14]
    assert summary["audit_error_status_codes"] == [7]
    assert summary["failure_categories"] == ["permission", "update_mask", "network"]
    assert summary["audit_error_message_terms"] == {
        "container": False,
        "name": False,
        "image": False,
        "traffic": False,
        "tag": False,
        "revision": True,
        "env": False,
        "secret": False,
        "update_mask": True,
        "port": False,
        "vpc": True,
        "quota": False,
        "immutable": True,
    }
    serialized = json.dumps(summary)
    for private_value in (
        PRIVATE_SERVICE,
        PRIVATE_NAME,
        PRIVATE_MESSAGE,
        "private-vpc",
        "private-revision-placeholder",
        "updateMask for VPC connector private-vpc",
    ):
        assert private_value not in serialized


@pytest.mark.parametrize(
    ("message", "present"),
    [
        ("multiple containers are unsupported on private-revision-placeholder", {"container", "revision"}),
        ("revision private-revision-placeholder is immutable", {"revision", "immutable"}),
        ("traffic target references tag private-tag on revision private-revision-placeholder", {"traffic", "tag", "revision"}),
        ("cannot update container name or image private-image-reference", {"container", "name", "image"}),
    ],
)
def test_audit_message_terms_are_closed_and_private_values_are_not_returned(message, present):
    service, revisions, policy, jobs, audit = _evidence(message)
    summary = diagnostic.summarize(
        service=service,
        revisions=revisions,
        expected_service=PRIVATE_SERVICE,
        expected_project=PRIVATE_PROJECT,
        expected_region=PRIVATE_REGION,
        audit_window_start=WINDOW_START,
        audit_window_end=WINDOW_END,
        policy=policy,
        jobs=jobs,
        audit_entries=audit,
        audit_status="ok",
    )
    terms = summary["audit_error_message_terms"]
    assert {term for term, value in terms.items() if value} == present
    assert all(type(value) is bool for value in terms.values())
    for private_value in (PRIVATE_SERVICE, PRIVATE_NAME, PRIVATE_MESSAGE, "private-image-reference", "private-tag"):
        assert private_value not in json.dumps(summary)


def test_audit_message_terms_ignore_events_outside_target():
    service, revisions, policy, jobs, audit = _evidence()
    summary = diagnostic.summarize(
        service=service,
        revisions=revisions,
        expected_service="different-service-placeholder",
        expected_project=PRIVATE_PROJECT,
        expected_region=PRIVATE_REGION,
        audit_window_start=WINDOW_START,
        audit_window_end=WINDOW_END,
        policy=policy,
        jobs=jobs,
        audit_entries=audit,
        audit_status="ok",
    )
    assert summary["target_matches"] is False
    assert not any(summary["audit_error_message_terms"].values())


def test_tag_budget_false_is_reported_as_boolean_without_service_name():
    service, revisions, policy, jobs, audit = _evidence()
    long_name = "s" * 45
    service["metadata"]["name"] = long_name
    summary = diagnostic.summarize(
        service=service,
        revisions=revisions,
        expected_service=long_name,
        expected_project=PRIVATE_PROJECT,
        expected_region=PRIVATE_REGION,
        audit_window_start=WINDOW_START,
        audit_window_end=WINDOW_END,
        policy=policy,
        jobs=jobs,
        audit_entries=audit,
        audit_status="ok",
    )
    assert summary["diagnostic_tag_budget_ok"] is False
    assert long_name not in json.dumps(summary)


def test_unreadable_metadata_stays_unknown_and_never_serializes_input():
    summary = diagnostic.summarize(
        service={"private": "private-value-placeholder"},
        revisions={"private": PRIVATE_NAME},
        expected_service=PRIVATE_SERVICE,
        expected_project=PRIVATE_PROJECT,
        expected_region=PRIVATE_REGION,
        audit_window_start=WINDOW_START,
        audit_window_end=WINDOW_END,
        policy=None,
        jobs=None,
        audit_entries=None,
        audit_status="unavailable",
    )
    assert summary["service_ready"] is None
    assert summary["latest_created_revision_found"] is False
    assert summary["revision_list_readable"] is False
    assert summary["iam_policy_readable"] is False
    assert summary["scheduler_readable"] is False
    assert summary["audit_query_status"] == "unavailable"
    assert PRIVATE_NAME not in json.dumps(summary)


def test_audit_events_must_match_exact_project_region_service_and_time_window():
    service, revisions, policy, jobs, audit = _evidence()
    same_prefix = json.loads(json.dumps(audit[0]))
    same_prefix["protoPayload"]["resourceName"] = (
        f"projects/{PRIVATE_PROJECT}/locations/{PRIVATE_REGION}/services/{PRIVATE_SERVICE}-suffix"
    )
    same_prefix["resource"]["labels"]["service_name"] = f"{PRIVATE_SERVICE}-suffix"
    other_region = json.loads(json.dumps(audit[0]))
    other_region["protoPayload"]["resourceName"] = (
        f"projects/{PRIVATE_PROJECT}/locations/other-region/services/{PRIVATE_SERVICE}"
    )
    other_region["resource"]["labels"]["location"] = "other-region"
    outside_window = json.loads(json.dumps(audit[0]))
    outside_window["timestamp"] = "2026-10-02T00:10:01Z"
    summary = diagnostic.summarize(
        service=service,
        revisions=revisions,
        expected_service=PRIVATE_SERVICE,
        expected_project=PRIVATE_PROJECT,
        expected_region=PRIVATE_REGION,
        audit_window_start=WINDOW_START,
        audit_window_end=WINDOW_END,
        policy=policy,
        jobs=jobs,
        audit_entries=[*audit, same_prefix, other_region, outside_window],
        audit_status="ok",
    )
    assert summary["audit_entry_count"] == 1
    assert summary["audit_error_count"] == 1
    assert summary["audit_error_categories"] == ["image"]


def test_failure_window_is_utc_and_limited_to_thirty_minutes():
    end = datetime.now(UTC).replace(microsecond=0)
    start = end - timedelta(minutes=30)
    diagnostic._validate_window(start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z"))
    with pytest.raises(ValueError, match="time_window_invalid"):
        diagnostic._validate_window(
            (end - timedelta(minutes=31)).isoformat().replace("+00:00", "Z"),
            end.isoformat().replace("+00:00", "Z"),
        )


def test_workflow_is_opt_in_read_only_and_reuses_existing_identity():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/diagnose-cached-stage-failure.yml").read_text(
        encoding="utf-8"
    )
    assert "default: false" in workflow
    assert "inputs.run_readonly_diagnostic == true" in workflow
    assert "refs/heads/main" in workflow
    assert "secrets.CLOUD_RUN_SERVICE" in workflow
    assert "vars.CLOUD_RUN_REGION" in workflow
    assert "GCP_PROJECT_ID: firstradequant" in workflow
    assert "google-github-actions/auth@v3" in workflow
    assert "firstrade-platform-deploy@firstradequant.iam.gserviceaccount.com" in workflow
    assert "github-main" in workflow
    assert "gcloud run services describe" in workflow
    assert "gcloud run revisions list" in workflow
    assert "gcloud run services get-iam-policy" in workflow
    assert "gcloud scheduler jobs list" in workflow
    assert "gcloud logging read" in workflow
    assert 'projects/${GCP_PROJECT_ID}/locations/${CLOUD_RUN_REGION}/services/${CLOUD_RUN_SERVICE}' in workflow
    assert "resource.labels.project_id=\\\"${GCP_PROJECT_ID}\\\"" in workflow
    assert "resource.labels.location=\\\"${CLOUD_RUN_REGION}\\\"" in workflow
    assert "gcloud run deploy" not in workflow
    assert "docker build" not in workflow
    assert "docker push" not in workflow
    assert "actions/upload-artifact" not in workflow
    assert workflow.count("python3 -m scripts.diagnose_cached_stage_failure") == 2
    assert "python3 scripts/diagnose_cached_stage_failure.py" not in workflow


def test_module_cli_runs_from_repository_root_without_pythonpath(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    service, revisions, policy, jobs, audit = _evidence()
    now = datetime.now(UTC).replace(microsecond=0)
    audit[0]["timestamp"] = (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    inputs = {
        "service": service,
        "revisions": revisions,
        "iam-policy": policy,
        "scheduler-jobs": jobs,
        "audit-entries": audit,
    }
    paths = {}
    for name, value in inputs.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        paths[name] = str(path)

    window_end = now.isoformat().replace("+00:00", "Z")
    window_start = (now - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}

    validated = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.diagnose_cached_stage_failure",
            "--validate-window",
            window_start,
            window_end,
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert validated.returncode == 0, validated.stderr
    assert validated.stdout == ""

    summarized = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.diagnose_cached_stage_failure",
            "--service",
            paths["service"],
            "--revisions",
            paths["revisions"],
            "--expected-service",
            PRIVATE_SERVICE,
            "--expected-project",
            PRIVATE_PROJECT,
            "--expected-region",
            PRIVATE_REGION,
            "--window-start",
            window_start,
            "--window-end",
            window_end,
            "--iam-policy",
            paths["iam-policy"],
            "--scheduler-jobs",
            paths["scheduler-jobs"],
            "--audit-entries",
            paths["audit-entries"],
            "--audit-status",
            "ok",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert summarized.returncode == 0, summarized.stderr
    summary = json.loads(summarized.stdout)
    assert summary["failure_source"] == "audit"
    assert summary["failure_categories"] == ["image"]
    assert PRIVATE_SERVICE not in summarized.stdout
    assert PRIVATE_MESSAGE not in summarized.stdout
