from __future__ import annotations

from copy import deepcopy
import json

import pytest

from scripts.account_facts_config_sync import (
    ConfigSyncError,
    desired_facts_config,
    verify_after,
    verify_before,
    verify_revision,
)


EXPECTED_SHA = "a" * 40
SERVICE = "firstrade-platform-service"
SECRET_NAME = "firstrade-account-facts-sync"
PROJECT_ID = "firstradequant"
PROJECT_NUMBER = "1088907247379"


def _desired(monkeypatch, **overrides):
    values = {
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED": "false",
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_URL": "https://qsl-strategy-switch-console.pigbibi.workers.dev/api/account-facts/sync",
        "FIRSTRADE_ACCOUNT_FACTS_TARGET_ID": "synthetic-target",
        "FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID": "b" * 64,
        "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_KEY": "synthetic-account-key",
        "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE": "US",
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN_SECRET_NAME": SECRET_NAME,
        "GCP_PROJECT_ID": PROJECT_ID,
        "GCP_PROJECT_NUMBER": PROJECT_NUMBER,
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return desired_facts_config()


def _service(
    *,
    facts_env=None,
    traffic=None,
    runtime_target=None,
    revision_name="firstrade-platform-service-candidate",
    spec_traffic=None,
    client_version="synthetic-gcloud-v1",
    template_annotations=None,
    volumes=None,
):
    target = runtime_target or {
        "platform_id": "firstrade",
        "service_name": SERVICE,
        "account_scope": "US",
        "account_selector": ["synthetic-native-account"],
    }
    env = [
        {"name": "RUNTIME_TARGET_JSON", "value": json.dumps(target)},
        {"name": "FIRSTRADE_ACCOUNT", "value": "synthetic-native-account"},
        {"name": "NOTIFY_LANG", "value": "en"},
    ]
    for row in facts_env or []:
        env = [existing for existing in env if existing["name"] != row["name"]]
        env.append(row)
    annotations = {
        "run.googleapis.com/client-name": "gcloud",
        "run.googleapis.com/client-version": client_version,
        **(template_annotations or {}),
    }
    return {
        "spec": {
            "traffic": spec_traffic
            if spec_traffic is not None
            else [{"revisionName": "firstrade-platform-service-old", "percent": 100}],
            "template": {
                "metadata": {"name": revision_name, "annotations": annotations},
                "spec": {
                    "serviceAccountName": "runtime-service-account",
                    "containers": [{"image": "example.invalid/image@sha256:synthetic", "env": env}],
                    **({"volumes": volumes} if volumes is not None else {}),
                }
            },
        },
        "status": {
            "latestCreatedRevisionName": revision_name,
            "latestReadyRevisionName": revision_name,
            "traffic": traffic or [{"revisionName": "firstrade-platform-service-old", "percent": 100}],
        },
    }


def _desired_entries(desired):
    return [
        {"name": name, "value": desired[name]}
        for name in (
            "FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED",
            "FIRSTRADE_ACCOUNT_FACTS_SYNC_URL",
            "FIRSTRADE_ACCOUNT_FACTS_TARGET_ID",
            "FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID",
            "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_KEY",
            "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE",
        )
    ] + [
        {
            "name": "FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN",
            "valueFrom": {"secretKeyRef": {"name": SECRET_NAME, "key": "latest"}},
        }
    ]


def _alias_secret_entries(desired, alias="facts-token-lookup"):
    entries = _desired_entries(desired)
    entries[-1] = {
        "name": "FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN",
        "valueFrom": {"secretKeyRef": {"name": alias, "key": "latest"}},
    }
    return entries


def _secret_mapping(alias="facts-token-lookup", *, project_number=PROJECT_NUMBER, secret_name=SECRET_NAME):
    return f"{alias}:projects/{project_number}/secrets/{secret_name}"


def test_config_requires_all_fields_and_approved_destination(monkeypatch):
    monkeypatch.delenv("FIRSTRADE_ACCOUNT_FACTS_SYNC_URL", raising=False)
    with pytest.raises(ConfigSyncError, match="configuration_incomplete"):
        desired_facts_config()

    with pytest.raises(ConfigSyncError, match="destination_not_approved"):
        _desired(monkeypatch, FIRSTRADE_ACCOUNT_FACTS_SYNC_URL="https://example.invalid/api")


def test_only_exact_six_fields_and_secret_reference_change_on_no_traffic_candidate(monkeypatch):
    desired = _desired(monkeypatch)
    before = _service()
    baseline = verify_before(before, SERVICE)
    after = _service(facts_env=_desired_entries(desired))

    verify_after(after, SERVICE, desired, SECRET_NAME, baseline)


def test_new_same_project_secret_lookup_alias_is_normalized_without_ignoring_other_mappings(monkeypatch):
    desired = _desired(monkeypatch)
    retained_mapping = _secret_mapping("existing-alert", secret_name="existing-alert-secret")
    before = _service(template_annotations={"run.googleapis.com/secrets": retained_mapping})
    baseline = verify_before(before, SERVICE)
    after = _service(
        facts_env=_alias_secret_entries(desired),
        template_annotations={
            "run.googleapis.com/secrets": f"{retained_mapping},{_secret_mapping()}"
        },
    )

    verify_after(after, SERVICE, desired, SECRET_NAME, baseline)


@pytest.mark.parametrize(
    "mapping",
    [
        _secret_mapping(secret_name="different-secret"),
        _secret_mapping(project_number="9999999999999"),
    ],
)
def test_facts_token_alias_must_resolve_to_expected_secret_in_current_project(monkeypatch, mapping):
    desired = _desired(monkeypatch)
    baseline = verify_before(_service(), SERVICE)
    after = _service(
        facts_env=_alias_secret_entries(desired),
        template_annotations={"run.googleapis.com/secrets": mapping},
    )

    with pytest.raises(ConfigSyncError, match="facts_secret_reference_readback_mismatch"):
        verify_after(after, SERVICE, desired, SECRET_NAME, baseline)


@pytest.mark.parametrize("reference_kind", ["environment", "volume"])
def test_facts_token_alias_cannot_be_shared_with_another_resource(monkeypatch, reference_kind):
    desired = _desired(monkeypatch)
    baseline = verify_before(_service(), SERVICE)
    entries = _alias_secret_entries(desired)
    volumes = None
    if reference_kind == "environment":
        entries.append(
            {
                "name": "OTHER_SECRET",
                "valueFrom": {"secretKeyRef": {"name": "facts-token-lookup", "key": "latest"}},
            }
        )
    else:
        volumes = [{"name": "existing-secret", "secret": {"secretName": "facts-token-lookup"}}]
    after = _service(
        facts_env=entries,
        template_annotations={"run.googleapis.com/secrets": _secret_mapping()},
        volumes=volumes,
    )

    with pytest.raises(ConfigSyncError, match="facts_secret_alias_shared"):
        verify_after(after, SERVICE, desired, SECRET_NAME, baseline)


def test_other_secret_alias_changes_remain_visible_to_template_readback(monkeypatch):
    desired = _desired(monkeypatch)
    before = _service(
        template_annotations={
            "run.googleapis.com/secrets": _secret_mapping(
                "retained-alias", secret_name="retained-secret"
            )
        }
    )
    baseline = verify_before(before, SERVICE)
    after = _service(
        facts_env=_alias_secret_entries(desired),
        template_annotations={
            "run.googleapis.com/secrets": ",".join(
                (
                    _secret_mapping("facts-token-lookup"),
                    _secret_mapping("retained-alias", secret_name="changed-secret"),
                )
            )
        },
    )

    with pytest.raises(ConfigSyncError, match="unrelated_service_configuration_changed"):
        verify_after(after, SERVICE, desired, SECRET_NAME, baseline)


@pytest.mark.parametrize(
    ("changed_env", "reason"),
    [
        ({"name": "NOTIFY_LANG", "value": "zh"}, "unrelated_service_configuration_changed"),
        (
            {
                "name": "FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN",
                "valueFrom": {"secretKeyRef": {"name": SECRET_NAME, "key": "latest"}},
                "value": "must-not-be-plain",
            },
            "facts_secret_reference_readback_mismatch",
        ),
        (
            {"name": "FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN", "valueFrom": "unexpected"},
            "facts_secret_reference_readback_mismatch",
        ),
    ],
)
def test_readback_rejects_unrelated_or_plain_token_changes(monkeypatch, changed_env, reason):
    desired = _desired(monkeypatch)
    baseline = verify_before(_service(), SERVICE)
    base_env = _service()["spec"]["template"]["spec"]["containers"][0]["env"]
    env = [row for row in base_env if row["name"] != changed_env["name"]]
    env.extend(row for row in _desired_entries(desired) if row["name"] != changed_env["name"])
    env.append(changed_env)
    after = _service(facts_env=env)

    with pytest.raises(ConfigSyncError, match=reason):
        verify_after(after, SERVICE, desired, SECRET_NAME, baseline)


def test_readback_rejects_token_secret_name_mismatch(monkeypatch):
    desired = _desired(monkeypatch)
    baseline = verify_before(_service(), SERVICE)
    entries = _desired_entries(desired)
    entries[-1]["valueFrom"]["secretKeyRef"]["name"] = "other-secret"

    with pytest.raises(ConfigSyncError, match="facts_secret_reference_readback_mismatch"):
        verify_after(_service(facts_env=entries), SERVICE, desired, SECRET_NAME, baseline)


@pytest.mark.parametrize(
    "changed_traffic",
    [
        [{"revisionName": "candidate", "percent": 100}],
        [{"revisionName": "firstrade-platform-service-old", "percent": 50}],
        [{"revisionName": "firstrade-platform-service-old", "percent": 100, "tag": "new-tag"}],
    ],
)
def test_readback_rejects_active_traffic_change(monkeypatch, changed_traffic):
    desired = _desired(monkeypatch)
    baseline = verify_before(_service(), SERVICE)
    after = _service(facts_env=_desired_entries(desired), traffic=changed_traffic)

    with pytest.raises(ConfigSyncError, match="traffic_changed"):
        verify_after(after, SERVICE, desired, SECRET_NAME, baseline)


def test_readback_rejects_service_traffic_configuration_change(monkeypatch):
    desired = _desired(monkeypatch)
    before = _service()
    baseline = verify_before(before, SERVICE)
    after = _service(facts_env=_desired_entries(desired))
    after["spec"]["traffic"] = [{"revisionName": "candidate", "percent": 100}]

    with pytest.raises(ConfigSyncError, match="traffic_changed"):
        verify_after(after, SERVICE, desired, SECRET_NAME, baseline)


def test_readback_rejects_latest_traffic_target_after_new_revision(monkeypatch):
    desired = _desired(monkeypatch)
    before = _service(
        revision_name="firstrade-platform-service-current",
        spec_traffic=[{"latestRevision": True, "percent": 100}],
    )
    baseline = verify_before(before, SERVICE)
    after = _service(
        facts_env=_desired_entries(desired),
        revision_name="firstrade-platform-service-new",
        spec_traffic=[{"latestRevision": True, "percent": 100}],
    )

    with pytest.raises(ConfigSyncError, match="traffic_changed"):
        verify_after(after, SERVICE, desired, SECRET_NAME, baseline)


def test_no_traffic_latest_to_fixed_revision_and_generated_metadata_are_equivalent(monkeypatch):
    desired = _desired(monkeypatch)
    status_traffic = [
        {"revisionName": "firstrade-platform-service-current", "percent": 100, "tag": "stable"}
    ]
    before = _service(
        revision_name="firstrade-platform-service-current",
        spec_traffic=[{"latestRevision": True, "percent": 100, "tag": "stable"}],
        traffic=status_traffic,
        template_annotations={"deployment.example.com/owner": "protected"},
    )
    baseline = verify_before(before, SERVICE)
    after = _service(
        facts_env=_desired_entries(desired),
        revision_name="firstrade-platform-service-new",
        spec_traffic=[
            {
                "revisionName": "firstrade-platform-service-current",
                "percent": 100,
                "tag": "stable",
            }
        ],
        traffic=status_traffic,
        client_version="synthetic-gcloud-v2",
        template_annotations={"deployment.example.com/owner": "protected"},
    )

    verify_after(after, SERVICE, desired, SECRET_NAME, baseline)


def test_readback_rejects_non_facts_template_change(monkeypatch):
    desired = _desired(monkeypatch)
    baseline = verify_before(_service(), SERVICE)
    after = _service(facts_env=_desired_entries(desired))
    after["spec"]["template"]["spec"]["containers"][0]["image"] = "example.invalid/changed@sha256:synthetic"

    with pytest.raises(ConfigSyncError, match="unrelated_service_configuration_changed"):
        verify_after(after, SERVICE, desired, SECRET_NAME, baseline)


def test_readback_does_not_ignore_non_client_template_annotations(monkeypatch):
    desired = _desired(monkeypatch)
    baseline = verify_before(
        _service(template_annotations={"deployment.example.com/owner": "before"}),
        SERVICE,
    )
    after = _service(
        facts_env=_desired_entries(desired),
        template_annotations={"deployment.example.com/owner": "after"},
    )

    with pytest.raises(ConfigSyncError, match="unrelated_service_configuration_changed"):
        verify_after(after, SERVICE, desired, SECRET_NAME, baseline)


@pytest.mark.parametrize(
    ("target_patch", "reason"),
    [
        ({"platform_id": "schwab"}, "runtime_target_mismatch"),
        ({"service_name": "other-service"}, "runtime_target_mismatch"),
        ({"account_scope": "HK"}, "runtime_target_scope_mismatch"),
        ({"account_selector": ["one", "two"]}, "runtime_target_account_selector_invalid"),
    ],
)
def test_before_refuses_unmatched_or_ambiguous_runtime_target(monkeypatch, target_patch, reason):
    target = {
        "platform_id": "firstrade",
        "service_name": SERVICE,
        "account_scope": "US",
        "account_selector": ["synthetic-native-account"],
    }
    target.update(target_patch)
    _desired(monkeypatch)
    with pytest.raises(ConfigSyncError, match=reason):
        verify_before(_service(runtime_target=target), SERVICE)


def test_revision_requires_current_sha_run_metadata_and_ready_condition():
    revision = {
        "metadata": {"labels": {"commit-sha": EXPECTED_SHA, "github-run-id": "12345"}},
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
    }
    verify_revision(revision, EXPECTED_SHA)

    wrong_sha = deepcopy(revision)
    wrong_sha["metadata"]["labels"]["commit-sha"] = "b" * 40
    with pytest.raises(ConfigSyncError, match="revision_source_mismatch"):
        verify_revision(wrong_sha, EXPECTED_SHA)

    wrong_run = deepcopy(revision)
    wrong_run["metadata"]["labels"]["github-run-id"] = "not-a-run"
    with pytest.raises(ConfigSyncError, match="revision_source_mismatch"):
        verify_revision(wrong_run, EXPECTED_SHA)

    unready = deepcopy(revision)
    unready["status"]["conditions"][0]["status"] = "False"
    with pytest.raises(ConfigSyncError, match="latest_revision_not_ready"):
        verify_revision(unready, EXPECTED_SHA)
