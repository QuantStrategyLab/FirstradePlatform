from __future__ import annotations

import copy
import io
import json

import pytest

from scripts import verify_cached_diagnostic_stage as stage

EXPECTED_SERVICE = "firstrade-service-placeholder"
EXPECTED_SOURCE = "e0043ca860a36c1790ddbb866cb848e298a3d3c7"


def test_config_difference_groups_never_expose_values_or_dynamic_keys():
    serving = {
        "serviceAccountName": "private-placeholder@example.invalid",
        "containers": [{"image": "old", "env": [{"name": "PRIVATE_PLACEHOLDER", "value": "private-value"}]}],
        "private-dynamic-key": "private-value",
    }
    desired = {
        "serviceAccountName": "other-placeholder@example.invalid",
        "containers": [{"image": "new", "env": []}],
    }
    assert stage._config_difference_groups(serving, desired) == ["other_spec", "primary_env", "service_account"]


def test_container_difference_categories_keep_private_details_closed():
    serving = {"containers": [{"name": "private-placeholder", "resources": {"limits": {"memory": "1Gi"}}, "unknown-private-key": "private-value"}]}
    desired = {"containers": [{"ports": [{"containerPort": 8080}]}]}
    assert stage._config_difference_groups(serving, desired) == [
        "primary_name", "primary_other", "primary_ports", "primary_resources"
    ]


def test_primary_name_shape_classifies_names_images_counts_and_dependencies():
    serving = {"containers": [
        {"name": "hidden-serving-app-1", "image": "registry.invalid/team/hidden-serving-app:stable"},
        {"name": "hidden-sidecar", "image": "registry.invalid/team/sidecar@sha256:" + "a" * 64},
    ]}
    desired = {"containers": [
        {"name": "hidden-desired-app", "image": "registry.invalid/team/hidden-desired-app@sha256:" + "b" * 64}
    ]}
    diagnostic = stage._primary_name_shape_diagnostic(
        serving,
        desired,
        serving_document={"spec": serving},
        desired_document={
            "metadata": {"annotations": {
                "run.googleapis.com/container-dependencies": '{"hidden-desired-app":["hidden-sidecar"]}'
            }},
            "spec": desired,
        },
    )
    assert diagnostic == {
        "serving_name": "present",
        "desired_name": "present",
        "serving_image_relation": "image_basename_numbered_suffix",
        "desired_image_relation": "matches_image_basename",
        "serving_container_count": "multiple",
        "desired_container_count": "single",
        "container_dependency_reference": "present",
    }
    assert not any("hidden-" in value for value in diagnostic.values())


@pytest.mark.parametrize(
    ("serving_container", "desired_container", "expected"),
    [
        ({"image": "registry.invalid/app:tag"}, {"name": "", "image": "registry.invalid/other"}, ("absent", "empty")),
        ({"name": None, "image": "app@sha256:" + "c" * 64}, {"name": 4, "image": "other:tag"}, ("invalid_type", "invalid_type")),
    ],
)
def test_primary_name_shape_reports_absent_empty_and_invalid_names(
    serving_container, desired_container, expected
):
    serving = {"containers": [serving_container]}
    desired = {"containers": [desired_container]}
    diagnostic = stage._primary_name_shape_diagnostic(
        serving, desired,
        serving_document={"spec": serving},
        desired_document={"spec": desired},
    )
    assert (diagnostic["serving_name"], diagnostic["desired_name"]) == expected
    assert diagnostic["container_dependency_reference"] == "absent"


def test_primary_name_mismatch_keeps_blocking_and_adds_only_closed_diagnostic():
    service = _service()
    revision = _revision()
    revision["spec"]["containers"][0].update({
        "name": "private-serving-name-1",
        "image": "registry.invalid/private/private-serving-name:tag",
    })
    service["spec"]["template"]["spec"]["containers"][0].update({
        "name": "private-desired-name",
        "image": "registry.invalid/private/private-desired-name@sha256:" + "d" * 64,
    })
    with pytest.raises(ValueError) as error:
        stage._validate_serving_revision(
            service, revision,
            expected_service=EXPECTED_SERVICE,
            expected_source_sha=EXPECTED_SOURCE,
        )
    message = str(error.value)
    assert "active_revision_service_config_mismatch:primary_name" in message
    assert '"serving_name":"present"' in message
    assert '"desired_name":"present"' in message
    assert "image_basename_numbered_suffix" in message
    assert "matches_image_basename" in message
    assert "private-serving-name" not in message
    assert "private-desired-name" not in message
    assert "registry.invalid" not in message


def _target(selector: str = "synthetic-account-placeholder") -> dict:
    return {
        "platform_id": "firstrade",
        "service_name": EXPECTED_SERVICE,
        "account_selector": [selector],
    }


def _service(*, app_env: list[dict] | None = None) -> dict:
    if app_env is None:
        app_env = [
            {"name": stage.DIAGNOSTIC_GATE, "value": "true"},
            {"name": "RUNTIME_TARGET_JSON", "value": json.dumps(_target())},
        ]
    return {
        "metadata": {
            "name": EXPECTED_SERVICE,
            "annotations": {"run.googleapis.com/ingress": "internal"},
        },
        "spec": {
            "traffic": [{"revisionName": "revision-old-placeholder", "percent": 100}],
            "template": {
                "metadata": {
                    "name": f"{EXPECTED_SERVICE}-00001-old",
                    "annotations": {
                        "run.googleapis.com/execution-environment": "gen2",
                        "run.googleapis.com/client-name": "gcloud-old",
                        "run.googleapis.com/client-version": "old-version",
                    },
                },
                "spec": {
                    "serviceAccountName": "runtime-placeholder@example.invalid",
                    "timeoutSeconds": 300,
                    "containers": [
                        {
                            "name": "app",
                            "image": "candidate-placeholder:old",
                            "env": app_env,
                            "volumeMounts": [{"name": "session-cache", "mountPath": "/tmp/session"}],
                        },
                        {"name": "sidecar", "image": "sidecar-placeholder@sha256:" + "d" * 64},
                    ],
                    "volumes": [{"name": "session-cache", "emptyDir": {}}],
                },
            }
        },
        "status": {
            "latestCreatedRevisionName": "revision-old-placeholder",
            "traffic": [{"revisionName": "revision-old-placeholder", "percent": 100}],
        },
    }


def _revision(
    *,
    source: str = EXPECTED_SOURCE,
    env: list[dict] | None = None,
) -> dict:
    revision_spec = copy.deepcopy(_service()["spec"]["template"]["spec"])
    if env is not None:
        revision_spec["containers"][0]["env"] = env
    return {
        "metadata": {"name": "revision-old-placeholder", "labels": {"commit-sha": source}},
        "spec": revision_spec,
    }


def _capture(monkeypatch, tmp_path, *, service=None, revision=None):
    state_path = tmp_path / "state.json"
    revision_path = tmp_path / "revision.json"
    revision_path.parent.mkdir(parents=True, exist_ok=True)
    revision_path.write_text(json.dumps(revision or _revision()), encoding="utf-8")
    monkeypatch.setattr(stage.sys, "stdin", io.StringIO(json.dumps(service or _service())))
    stage.capture_baseline(
        state_path,
        revision_path,
        expected_service=EXPECTED_SERVICE,
        expected_source_sha=EXPECTED_SOURCE,
    )
    return state_path


def _staged_service(*, image: str = "registry.invalid/app@sha256:" + "a" * 64) -> dict:
    staged = copy.deepcopy(_service())
    staged["spec"]["traffic"] = [
        {"revisionName": "revision-old-placeholder", "percent": 100},
        {
            "revisionName": "revision-new-placeholder",
            "percent": 0,
            "tag": stage.DIAGNOSTIC_TAG,
        },
    ]
    staged["spec"]["template"]["spec"]["containers"][0]["image"] = image
    staged["spec"]["template"]["metadata"]["name"] = f"{EXPECTED_SERVICE}-00002-new"
    staged["spec"]["template"]["metadata"]["annotations"]["run.googleapis.com/client-name"] = "gcloud-new"
    staged["spec"]["template"]["metadata"]["annotations"]["run.googleapis.com/client-version"] = "new-version"
    staged["status"]["latestCreatedRevisionName"] = "revision-new-placeholder"
    staged["status"]["traffic"].append(
        {
            "revisionName": "revision-new-placeholder",
            "percent": 0,
            "tag": stage.DIAGNOSTIC_TAG,
            "url": "https://tagged-service-placeholder.invalid",
        }
    )
    return staged


def test_stage_preserves_real_config_and_sidecar_images_while_primary_image_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    state_path = _capture(monkeypatch, tmp_path)
    staged = _staged_service()
    monkeypatch.setattr(stage.sys, "stdin", io.StringIO(json.dumps(staged)))
    stage.verify_readback(state_path, staged["spec"]["template"]["spec"]["containers"][0]["image"])

    changed_sidecar = copy.deepcopy(staged)
    changed_sidecar["spec"]["template"]["spec"]["containers"][1]["image"] = "sidecar-placeholder@sha256:" + "e" * 64
    monkeypatch.setattr(stage.sys, "stdin", io.StringIO(json.dumps(changed_sidecar)))
    with pytest.raises(ValueError, match="service_configuration_changed"):
        stage.verify_readback(state_path, changed_sidecar["spec"]["template"]["spec"]["containers"][0]["image"])


def test_stage_allows_frozen_original_revision_and_single_zero_percent_diagnostic_tag(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    service = _service()
    service["spec"]["traffic"] = [
        {"latestRevision": True, "percent": 100},
        {"revisionName": "revision-old-placeholder", "percent": 0, "tag": "existing-placeholder"},
    ]
    state_path = _capture(monkeypatch, tmp_path, service=service)

    staged = _staged_service()
    staged["spec"]["traffic"] = [
        {"revisionName": "revision-old-placeholder", "percent": 100},
        {"revisionName": "revision-old-placeholder", "percent": 0, "tag": "existing-placeholder"},
        {
            "revisionName": "revision-new-placeholder",
            "percent": 0,
            "tag": stage.DIAGNOSTIC_TAG,
        },
    ]
    monkeypatch.setattr(stage.sys, "stdin", io.StringIO(json.dumps(staged)))
    stage.verify_readback(state_path, staged["spec"]["template"]["spec"]["containers"][0]["image"])


@pytest.mark.parametrize(
    ("traffic", "error"),
    [
        (
            [
                {"revisionName": "revision-old-placeholder", "percent": 90},
                {"revisionName": "revision-new-placeholder", "percent": 0, "tag": stage.DIAGNOSTIC_TAG},
            ],
            "desired_traffic_not_active_100",
        ),
        (
            [
                {"revisionName": "revision-old-placeholder", "percent": 100},
                {"revisionName": "revision-new-placeholder", "percent": 0, "tag": stage.DIAGNOSTIC_TAG},
                {"revisionName": "revision-old-placeholder", "percent": 0, "tag": "foreign-placeholder"},
            ],
            "desired_traffic_changed",
        ),
        (
            [
                {"latestRevision": True, "percent": 100},
                {"revisionName": "revision-new-placeholder", "percent": 0, "tag": stage.DIAGNOSTIC_TAG},
            ],
            "desired_traffic_not_frozen",
        ),
    ],
)
def test_stage_rejects_changed_or_unfrozen_desired_traffic(monkeypatch, tmp_path, traffic, error):
    service = _service()
    service["spec"]["traffic"] = [{"latestRevision": True, "percent": 100}]
    state_path = _capture(monkeypatch, tmp_path, service=service)
    staged = _staged_service()
    staged["spec"]["traffic"] = traffic
    monkeypatch.setattr(stage.sys, "stdin", io.StringIO(json.dumps(staged)))
    with pytest.raises(ValueError, match=error):
        stage.verify_readback(state_path, staged["spec"]["template"]["spec"]["containers"][0]["image"])


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda doc: doc["metadata"].update({"name": "other-service-placeholder"}), "service_target_mismatch"),
        (lambda doc: doc["metadata"]["annotations"].update({"run.googleapis.com/ingress": "all"}), "ingress_not_internal"),
        (lambda doc: doc["status"]["traffic"][0].update({"percent": 99}), "traffic_not_single_revision_100"),
        (
            lambda doc: doc["spec"]["template"]["spec"]["containers"][0]["env"].append(
                {"name": stage.DIAGNOSTIC_GATE, "value": "false"}
            ),
            "diagnostic_gate_ambiguous",
        ),
        (
            lambda doc: doc["spec"]["template"]["spec"]["containers"][0]["env"][0].update({"value": "false"}),
            "diagnostic_gate_explicitly_disabled_or_invalid",
        ),
    ],
)
def test_stage_rejects_unsafe_service_metadata(monkeypatch, tmp_path, mutate, error):
    service = _service()
    mutate(service)
    with pytest.raises(ValueError, match=error):
        _capture(monkeypatch, tmp_path, service=service)


def test_stage_rejects_service_configuration_drift(monkeypatch: pytest.MonkeyPatch, tmp_path):
    state_path = _capture(monkeypatch, tmp_path)
    staged = _staged_service()
    staged["spec"]["template"]["spec"]["volumes"][0]["emptyDir"]["medium"] = "Memory"
    monkeypatch.setattr(stage.sys, "stdin", io.StringIO(json.dumps(staged)))
    with pytest.raises(ValueError, match="service_configuration_changed"):
        stage.verify_readback(state_path, staged["spec"]["template"]["spec"]["containers"][0]["image"])


def test_stage_rejects_missing_desired_tag_even_if_observed_tag_remains(monkeypatch, tmp_path):
    state_path = _capture(monkeypatch, tmp_path)
    staged = _staged_service()
    staged["spec"]["traffic"] = [{"revisionName": "revision-old-placeholder", "percent": 100}]
    monkeypatch.setattr(stage.sys, "stdin", io.StringIO(json.dumps(staged)))
    with pytest.raises(ValueError, match="diagnostic_tag_ambiguous"):
        stage.verify_readback(state_path, staged["spec"]["template"]["spec"]["containers"][0]["image"])


def test_stage_rejects_active_traffic_change(monkeypatch: pytest.MonkeyPatch, tmp_path):
    state_path = _capture(monkeypatch, tmp_path)
    staged = _staged_service()
    staged["status"]["traffic"][0]["revisionName"] = "revision-other-placeholder"
    monkeypatch.setattr(stage.sys, "stdin", io.StringIO(json.dumps(staged)))
    with pytest.raises(ValueError, match="active_traffic_changed"):
        stage.verify_readback(state_path, staged["spec"]["template"]["spec"]["containers"][0]["image"])


@pytest.mark.parametrize(
    ("revision", "error"),
    [
        (_revision(source="0" * 40), "active_revision_source_mismatch"),
        (_revision(env=[{"name": "RUNTIME_TARGET_JSON", "value": json.dumps({"platform_id": "other", "account_selector": ["x"]})}]), "runtime_target_platform_mismatch"),
        (_revision(env=[{"name": "RUNTIME_TARGET_JSON", "value": json.dumps({"platform_id": "firstrade", "service_name": "other", "account_selector": ["x"]})}]), "runtime_target_service_mismatch"),
        (_revision(env=[{"name": "RUNTIME_TARGET_JSON", "value": json.dumps({"platform_id": "firstrade", "account_selector": ["one", "two"]})}]), "account_binding_selector_not_unique"),
        (_revision(env=[]), "runtime_target_missing"),
    ],
)
def test_capture_requires_exact_serving_source_and_selector(monkeypatch, tmp_path, revision, error):
    with pytest.raises(ValueError, match=error):
        _capture(monkeypatch, tmp_path, revision=revision)


def test_capture_requires_service_template_to_match_serving_revision_binding(monkeypatch, tmp_path):
    service = _service()
    service["spec"]["template"]["spec"]["containers"][0]["env"] = [
        {"name": stage.DIAGNOSTIC_GATE, "value": "true"},
        {"name": "RUNTIME_TARGET_JSON", "value": json.dumps(_target("different-placeholder"))},
    ]
    with pytest.raises(ValueError, match="active_revision_binding_mismatch"):
        _capture(monkeypatch, tmp_path, service=service, revision=_revision())


def test_capture_uses_qsl_target_precedence_and_rejects_legacy_only_selector(monkeypatch, tmp_path):
    qsl_and_runtime = [
        {"name": "QSL_RUNTIME_TARGET_JSON", "value": json.dumps(_target("qsl-placeholder"))},
        {"name": "RUNTIME_TARGET_JSON", "value": json.dumps(_target("runtime-placeholder"))},
        {"name": "FIRSTRADE_ACCOUNT", "value": "legacy-placeholder"},
    ]
    _capture(
        monkeypatch,
        tmp_path,
        service=_service(app_env=[*qsl_and_runtime, {"name": stage.DIAGNOSTIC_GATE, "value": "true"}]),
        revision=_revision(env=[*qsl_and_runtime, {"name": stage.DIAGNOSTIC_GATE, "value": "true"}]),
    )

    legacy = [{"name": "FIRSTRADE_ACCOUNT", "value": "legacy-placeholder"}]
    with pytest.raises(ValueError, match="runtime_target_missing"):
        _capture(
            monkeypatch,
            tmp_path / "legacy",
            service=_service(app_env=[*legacy, {"name": stage.DIAGNOSTIC_GATE, "value": "true"}]),
            revision=_revision(env=[*legacy, {"name": stage.DIAGNOSTIC_GATE, "value": "true"}]),
        )


def test_capture_rejects_missing_or_secret_backed_selector():
    with pytest.raises(ValueError, match="runtime_target_missing"):
        stage._account_selector(
            {
                "FIRSTRADE_ACCOUNT": [
                    {"name": "FIRSTRADE_ACCOUNT", "value": "one"},
                ]
            },
            EXPECTED_SERVICE,
        )
    with pytest.raises(ValueError, match="account_binding_secret_ref_unresolved"):
        stage._account_selector(
            {
                "RUNTIME_TARGET_JSON": [
                    {"name": "RUNTIME_TARGET_JSON", "valueFrom": {"secretKeyRef": {"key": "current"}}}
                ]
            },
            EXPECTED_SERVICE,
        )


def test_scheduler_hash_preserves_body_oidc_retry_and_deadline_but_ignores_observation_fields():
    original = [
        {
            "name": "scheduler-placeholder",
            "state": "ENABLED",
            "schedule": "10 22 * * *",
            "timeZone": "UTC",
            "httpTarget": {
                "uri": "https://service-placeholder/account-balance-diagnostic",
                "httpMethod": "POST",
                "body": "",
                "oidcToken": {
                    "serviceAccountEmail": "scheduler-placeholder@example.invalid",
                    "audience": "https://service-placeholder",
                },
            },
            "retryConfig": {"retryCount": 0, "maxRetryDuration": "0s"},
            "attemptDeadline": "180s",
            "lastAttemptTime": "2026-01-01T00:00:00Z",
            "status": {"code": 0},
        }
    ]
    changed_observation = copy.deepcopy(original)
    changed_observation[0]["lastAttemptTime"] = "2026-01-02T00:00:00Z"
    changed_observation[0]["status"] = {"code": 5}
    assert stage._scheduler_jobs_hash(original) == stage._scheduler_jobs_hash(changed_observation)

    for field_update in (
        lambda job: job["httpTarget"].update({"body": "unexpected"}),
        lambda job: job["httpTarget"]["oidcToken"].update({"audience": "https://other-placeholder"}),
        lambda job: job["retryConfig"].update({"retryCount": 1}),
        lambda job: job.update({"attemptDeadline": "600s"}),
    ):
        changed_config = copy.deepcopy(original)
        field_update(changed_config[0])
        assert stage._scheduler_jobs_hash(original) != stage._scheduler_jobs_hash(changed_config)
