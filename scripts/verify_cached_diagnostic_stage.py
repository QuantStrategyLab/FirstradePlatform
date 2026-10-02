from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


DIAGNOSTIC_GATE = "FIRSTRADE_CACHED_BALANCE_DIAGNOSTIC_ON_HTTP"
RUNTIME_TARGET_KEYS = ("QSL_RUNTIME_TARGET_JSON", "RUNTIME_TARGET_JSON")
DIAGNOSTIC_TAG = "cbd"
MIN_TRAFFIC_TAG_LENGTH = 3
MAX_SERVICE_AND_TRAFFIC_TAG_LENGTH = 46
EXPECTED_PLATFORM_ID = "firstrade"
GENERATED_TEMPLATE_ANNOTATIONS = {
    "run.googleapis.com/client-name",
    "run.googleapis.com/client-version",
}
OBSERVATION_ONLY_SCHEDULER_FIELDS = {
    "createTime",
    "lastAttemptTime",
    "scheduleTime",
    "status",
    "updateTime",
    "userUpdateTime",
}


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _active_traffic(service: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    status = service.get("status")
    if not isinstance(status, dict):
        raise ValueError("service_status_missing")
    traffic_rows = status.get("traffic")
    if not isinstance(traffic_rows, list):
        raise ValueError("traffic_missing")
    active_traffic = []
    for row in traffic_rows:
        if not isinstance(row, dict):
            raise ValueError("traffic_invalid")
        try:
            percent = int(row.get("percent", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("traffic_invalid") from exc
        if percent > 0:
            revision = row.get("revisionName")
            if not isinstance(revision, str) or not revision:
                raise ValueError("traffic_revision_missing")
            active_traffic.append({"revisionName": revision, "percent": percent})
    if len(active_traffic) != 1 or active_traffic[0]["percent"] != 100:
        raise ValueError("traffic_not_single_revision_100")
    active_traffic.sort(key=lambda item: (item["revisionName"], item["percent"]))
    return active_traffic, traffic_rows


def _validate_diagnostic_tag_budget(service_name: str, tag: str) -> None:
    if not isinstance(service_name, str) or not service_name:
        raise ValueError("diagnostic_service_name_missing")
    if tag != DIAGNOSTIC_TAG:
        raise ValueError("diagnostic_tag_mismatch")
    if len(tag) < MIN_TRAFFIC_TAG_LENGTH:
        raise ValueError("diagnostic_tag_too_short")
    if len(service_name) + len(tag) > MAX_SERVICE_AND_TRAFFIC_TAG_LENGTH:
        raise ValueError("diagnostic_tag_name_budget_exceeded")


def _normalize_template_metadata(template: dict[str, Any]) -> None:
    metadata = template.get("metadata")
    if not isinstance(metadata, dict):
        return
    # Cloud Run assigns the revision name and these client annotations when a
    # new revision is created. All user-supplied metadata remains compared.
    metadata.pop("name", None)
    annotations = metadata.get("annotations")
    if isinstance(annotations, dict):
        for key in GENERATED_TEMPLATE_ANNOTATIONS:
            annotations.pop(key, None)


def _config_spec_hash(spec: dict[str, Any]) -> str:
    containers = spec.get("containers")
    if not isinstance(containers, list) or not containers:
        raise ValueError("containers_missing")
    normalized = copy.deepcopy(spec)
    for index, container in enumerate(containers):
        if not isinstance(container, dict):
            raise ValueError("container_invalid")
        if index == 0:
            normalized["containers"][index]["image"] = "<primary-app-image>"
    return _canonical_hash(normalized)


def _config_difference_groups(serving: dict[str, Any], desired: dict[str, Any]) -> list[str]:
    """Report only fixed configuration categories, never field values or names."""
    groups = []
    for key, label in (
        ("serviceAccountName", "service_account"),
        ("containerConcurrency", "concurrency"),
        ("timeoutSeconds", "timeout"),
        ("volumes", "volumes"),
    ):
        if serving.get(key) != desired.get(key):
            groups.append(label)
    left, right = serving.get("containers", []), desired.get("containers", [])
    if len(left) != len(right):
        groups.append("container_count")
    for index, (old, new) in enumerate(zip(left, right)):
        if old.get("env", []) != new.get("env", []):
            groups.append("primary_env" if index == 0 else "sidecar_env")
        ignored = {"env", "image"} if index == 0 else {"env"}
        prefix = "primary" if index == 0 else "sidecar"
        known_fields = {
            "name": "name",
            "command": "command",
            "args": "args",
            "resources": "resources",
            "ports": "ports",
            "volumeMounts": "volume_mounts",
            "startupProbe": "startup_probe",
            "livenessProbe": "liveness_probe",
            "workingDir": "working_dir",
        }
        for key, label in known_fields.items():
            if old.get(key) != new.get(key):
                groups.append(f"{prefix}_{label}")
        excluded = ignored | set(known_fields)
        if {k: v for k, v in old.items() if k not in excluded} != {
            k: v for k, v in new.items() if k not in excluded
        }:
            groups.append(f"{prefix}_other")
    ignored_spec = {"serviceAccountName", "containerConcurrency", "timeoutSeconds", "volumes", "containers"}
    if {k: v for k, v in serving.items() if k not in ignored_spec} != {
        k: v for k, v in desired.items() if k not in ignored_spec
    }:
        groups.append("other_spec")
    return sorted(set(groups))


def _container_name_status(container: Any) -> str:
    if not isinstance(container, dict) or "name" not in container:
        return "absent"
    name = container.get("name")
    if name == "":
        return "empty"
    return "present" if isinstance(name, str) else "invalid_type"


def _container_count_status(spec: dict[str, Any]) -> str:
    containers = spec.get("containers")
    if not isinstance(containers, list):
        return "invalid"
    return "empty" if not containers else "single" if len(containers) == 1 else "multiple"


def _name_image_relation(container: Any) -> str:
    if _container_name_status(container) != "present":
        return "unavailable"
    image = container.get("image")
    if not isinstance(image, str) or not image:
        return "image_unavailable"
    image_name = image.split("@", 1)[0].rsplit("/", 1)[-1].split(":", 1)[0]
    name = container["name"]
    if name == image_name:
        return "matches_image_basename"
    if re.fullmatch(re.escape(image_name) + r"-[0-9]+", name):
        return "image_basename_numbered_suffix"
    return "other"


def _container_dependency_status(*documents: dict[str, Any]) -> str:
    for document in documents:
        spec = document.get("spec")
        containers = spec.get("containers") if isinstance(spec, dict) else None
        if isinstance(containers, list):
            for container in containers:
                if isinstance(container, dict) and "dependsOn" in container:
                    dependencies = container["dependsOn"]
                    if not isinstance(dependencies, list):
                        return "invalid"
                    if dependencies:
                        return "present"
        metadata = document.get("metadata")
        annotations = metadata.get("annotations") if isinstance(metadata, dict) else None
        annotation_key = "run.googleapis.com/container-dependencies"
        if isinstance(annotations, dict) and annotation_key in annotations:
            annotation = annotations[annotation_key]
            try:
                dependencies = json.loads(annotation) if isinstance(annotation, str) else None
            except json.JSONDecodeError:
                dependencies = None
            if not isinstance(dependencies, dict) or any(
                not isinstance(value, list) for value in dependencies.values()
            ):
                return "invalid"
            if any(dependencies.values()):
                return "present"
    return "absent"


def _primary_name_shape_diagnostic(
    serving: dict[str, Any],
    desired: dict[str, Any],
    *,
    serving_document: dict[str, Any],
    desired_document: dict[str, Any],
) -> dict[str, str]:
    serving_containers = serving.get("containers")
    desired_containers = desired.get("containers")
    serving_valid = isinstance(serving_containers, list)
    desired_valid = isinstance(desired_containers, list)
    serving_containers = serving_containers if serving_valid else []
    desired_containers = desired_containers if desired_valid else []
    old_primary = serving_containers[0] if serving_containers else None
    new_primary = desired_containers[0] if desired_containers else None
    return {
        "serving_name": _container_name_status(old_primary),
        "desired_name": _container_name_status(new_primary),
        "serving_image_relation": _name_image_relation(old_primary),
        "desired_image_relation": _name_image_relation(new_primary),
        "serving_container_count": _container_count_status(serving),
        "desired_container_count": _container_count_status(desired),
        "container_dependency_reference": _container_dependency_status(
            serving_document, desired_document
        ),
    }


def _has_provider_default_primary_name(
    serving: dict[str, Any],
    desired: dict[str, Any],
    *,
    serving_document: dict[str, Any],
    desired_document: dict[str, Any],
) -> bool:
    serving_containers = serving.get("containers")
    desired_containers = desired.get("containers")
    return (
        _container_count_status(serving) == "single"
        and _container_count_status(desired) == "single"
        and isinstance(serving_containers, list)
        and isinstance(desired_containers, list)
        and (
            _container_name_status(desired_containers[0]) == "absent"
            or (
                _container_name_status(desired_containers[0]) == "present"
                and desired_containers[0].get("name") == serving_containers[0].get("name")
                and desired_containers[0].get("image") == serving_containers[0].get("image")
            )
        )
        and _container_name_status(serving_containers[0]) == "present"
        and _name_image_relation(serving_containers[0]) == "image_basename_numbered_suffix"
        and _container_dependency_status(serving_document, desired_document) == "absent"
    )


def _service_spec_for_name_mode(
    spec: dict[str, Any],
    mode: str,
    *,
    document: dict[str, Any],
    name_source_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if mode == "strict":
        return spec
    if mode != "provider_default":
        raise ValueError("baseline_invalid")
    template = spec.get("template")
    template_spec = template.get("spec") if isinstance(template, dict) else None
    if not isinstance(template_spec, dict) or not isinstance(name_source_spec, dict):
        raise ValueError("service_configuration_changed")
    containers = name_source_spec.get("containers")
    if (
        _container_count_status(name_source_spec) != "single"
        or not isinstance(containers, list)
        or _container_dependency_status(document) != "absent"
    ):
        raise ValueError("service_configuration_changed")
    name_status = _container_name_status(containers[0])
    if name_status not in ("absent", "present") or (
        name_status == "present"
        and _name_image_relation(containers[0]) != "image_basename_numbered_suffix"
    ):
        raise ValueError("service_configuration_changed")
    normalized = copy.deepcopy(spec)
    if name_status == "present":
        normalized["template"]["spec"]["containers"][0].pop("name")
    return normalized


def _desired_traffic_rows(service: dict[str, Any], *, allow_diagnostic_tag: bool) -> list[dict[str, Any]]:
    active, _ = _active_traffic(service)
    active_revision = active[0]["revisionName"]
    latest_created = (service.get("status") or {}).get("latestCreatedRevisionName")
    spec = service.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("service_shape_invalid")
    rows = spec.get("traffic")
    if rows is None:
        if allow_diagnostic_tag:
            raise ValueError("desired_traffic_not_frozen")
        rows = [{"revisionName": active_revision, "percent": 100}]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("desired_traffic_invalid")

    normalized: list[dict[str, Any]] = []
    diagnostic_rows = 0
    positive_rows: list[dict[str, Any]] = []
    for row in rows:
        item = copy.deepcopy(row)
        try:
            percent = int(item.get("percent", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("desired_traffic_invalid") from exc
        if item.get("tag") == DIAGNOSTIC_TAG:
            diagnostic_rows += 1
            if not allow_diagnostic_tag:
                raise ValueError("diagnostic_tag_already_present")
            if percent != 0 or item.get("revisionName") != latest_created:
                raise ValueError("diagnostic_tag_revision_invalid")
            continue

        if "latestRevision" in item:
            if allow_diagnostic_tag:
                raise ValueError("desired_traffic_not_frozen")
            if item.get("latestRevision") is not True or item.get("revisionName"):
                raise ValueError("desired_traffic_invalid")
            item.pop("latestRevision")
            item["revisionName"] = active_revision
        if not isinstance(item.get("revisionName"), str) or not item["revisionName"]:
            raise ValueError("desired_traffic_invalid")
        item["percent"] = percent
        if percent > 0:
            positive_rows.append(item)
        normalized.append(item)

    if diagnostic_rows > 1 or (allow_diagnostic_tag and diagnostic_rows != 1):
        raise ValueError("diagnostic_tag_ambiguous")
    if len(positive_rows) != 1 or positive_rows[0]["percent"] != 100:
        raise ValueError("desired_traffic_not_active_100")
    normalized.sort(key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":")))
    return normalized


def _service_summary(service: dict[str, Any], *, allow_diagnostic_tag: bool) -> dict[str, Any]:
    metadata = service.get("metadata")
    spec = service.get("spec")
    if not all(isinstance(value, dict) for value in (metadata, spec)):
        raise ValueError("service_shape_invalid")

    service_annotations = metadata.get("annotations")
    if not isinstance(service_annotations, dict):
        service_annotations = {}
    ingress = service_annotations.get("run.googleapis.com/ingress-status") or service_annotations.get(
        "run.googleapis.com/ingress"
    )
    if ingress != "internal":
        raise ValueError("ingress_not_internal")

    active_traffic, traffic_rows = _active_traffic(service)

    template = spec.get("template")
    if not isinstance(template, dict):
        raise ValueError("template_missing")
    template_spec = template.get("spec")
    if not isinstance(template_spec, dict):
        raise ValueError("template_spec_missing")
    containers = template_spec.get("containers")
    if not isinstance(containers, list) or not containers:
        raise ValueError("containers_missing")

    gate_values: list[Any] = []
    normalized_spec = copy.deepcopy(spec)
    normalized_spec.pop("traffic", None)
    normalized_template = normalized_spec.get("template")
    if not isinstance(normalized_template, dict):
        raise ValueError("template_missing")
    _normalize_template_metadata(normalized_template)
    normalized_containers = normalized_template.get("spec", {}).get("containers") or []
    for index, container in enumerate(containers):
        if not isinstance(container, dict):
            raise ValueError("container_invalid")
        if index == 0:
            normalized_containers[index]["image"] = "<primary-app-image>"
        env = container.get("env") or []
        if not isinstance(env, list):
            raise ValueError("container_env_invalid")
        for variable in env:
            if isinstance(variable, dict) and variable.get("name") == DIAGNOSTIC_GATE:
                gate_values.append(variable)
    if len(gate_values) > 1:
        raise ValueError("diagnostic_gate_ambiguous")
    if gate_values:
        gate = gate_values[0]
        if "value" not in gate or str(gate["value"]).strip().lower() != "true":
            raise ValueError("diagnostic_gate_explicitly_disabled_or_invalid")

    if not allow_diagnostic_tag and any(
        isinstance(row, dict) and row.get("tag") == DIAGNOSTIC_TAG for row in traffic_rows
    ):
        raise ValueError("diagnostic_tag_already_present")

    filtered_traffic = [
        row
        for row in traffic_rows
        if not (allow_diagnostic_tag and isinstance(row, dict) and row.get("tag") == DIAGNOSTIC_TAG)
    ]
    return {
        "active_traffic": active_traffic,
        "service_spec_without_primary_image": normalized_spec,
        "latest_created_revision": (service.get("status") or {}).get("latestCreatedRevisionName"),
        "traffic_rows": filtered_traffic,
        "all_traffic_rows": traffic_rows,
        "desired_traffic_rows": _desired_traffic_rows(service, allow_diagnostic_tag=allow_diagnostic_tag),
        "first_container_image": containers[0].get("image"),
    }


def _env_entries(revision: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    spec = revision.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("active_revision_spec_missing")
    containers = spec.get("containers")
    if not isinstance(containers, list) or not containers:
        raise ValueError("active_revision_containers_missing")
    entries: dict[str, list[dict[str, Any]]] = {}
    for container in containers:
        if not isinstance(container, dict):
            raise ValueError("active_revision_container_invalid")
        env = container.get("env") or []
        if not isinstance(env, list):
            raise ValueError("active_revision_env_invalid")
        for variable in env:
            if not isinstance(variable, dict):
                raise ValueError("active_revision_env_invalid")
            name = variable.get("name")
            if isinstance(name, str) and name in (*RUNTIME_TARGET_KEYS, DIAGNOSTIC_GATE):
                entries.setdefault(name, []).append(variable)
    return entries


def _validate_diagnostic_gate(entries: dict[str, list[dict[str, Any]]]) -> None:
    matches = entries.get(DIAGNOSTIC_GATE, [])
    if len(matches) > 1:
        raise ValueError("diagnostic_gate_ambiguous")
    if matches and (
        "value" not in matches[0]
        or str(matches[0]["value"]).strip().lower() != "true"
        or "valueFrom" in matches[0]
    ):
        raise ValueError("diagnostic_gate_explicitly_disabled_or_invalid")


def _literal_env(entries: dict[str, list[dict[str, Any]]], name: str) -> str | None:
    matches = entries.get(name, [])
    if len(matches) > 1:
        raise ValueError("account_binding_env_ambiguous")
    if not matches:
        return None
    value = matches[0]
    if "valueFrom" in value:
        raise ValueError("account_binding_secret_ref_unresolved")
    raw = value.get("value")
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ValueError("account_binding_env_invalid")
    return raw


def _account_selector(entries: dict[str, list[dict[str, Any]]], expected_service: str) -> str:
    raw_target = _literal_env(entries, RUNTIME_TARGET_KEYS[0]) or _literal_env(
        entries, RUNTIME_TARGET_KEYS[1]
    )
    if not raw_target:
        raise ValueError("runtime_target_missing")
    try:
        target = json.loads(raw_target)
    except json.JSONDecodeError as exc:
        raise ValueError("runtime_target_invalid") from exc
    if not isinstance(target, dict):
        raise ValueError("runtime_target_invalid")
    if target.get("platform_id") != EXPECTED_PLATFORM_ID:
        raise ValueError("runtime_target_platform_mismatch")
    service_name = target.get("service_name")
    if service_name is not None and str(service_name).strip() != expected_service:
        raise ValueError("runtime_target_service_mismatch")
    selector_value = target.get("account_selector")
    if isinstance(selector_value, str):
        selectors = [selector_value.strip()] if selector_value.strip() else []
    elif isinstance(selector_value, list):
        if any(item is not None and not isinstance(item, str) for item in selector_value):
            raise ValueError("runtime_target_selector_invalid")
        selectors = [item.strip() for item in selector_value if isinstance(item, str) and item.strip()]
    else:
        selectors = []
    if len(selectors) != 1:
        raise ValueError("account_binding_selector_not_unique")
    return selectors[0]


def _validate_serving_revision(
    service: dict[str, Any],
    revision: dict[str, Any],
    *,
    expected_service: str,
    expected_source_sha: str,
) -> str:
    metadata = service.get("metadata") or {}
    if metadata.get("name") != expected_service:
        raise ValueError("service_target_mismatch")
    active, _ = _active_traffic(service)
    revision_metadata = revision.get("metadata")
    if not isinstance(revision_metadata, dict):
        raise ValueError("active_revision_metadata_missing")
    revision_name = revision_metadata.get("name")
    if revision_name != active[0]["revisionName"]:
        raise ValueError("active_revision_readback_mismatch")
    revision_spec = revision.get("spec")
    if not isinstance(revision_spec, dict):
        raise ValueError("active_revision_spec_missing")
    template_metadata = revision_spec.get("metadata") or {}
    labels = [revision_metadata.get("labels") or {}, template_metadata.get("labels") or {}]
    source_values = [labels_item.get("commit-sha") for labels_item in labels if labels_item.get("commit-sha")]
    if not source_values or any(value != expected_source_sha for value in source_values):
        raise ValueError("active_revision_source_mismatch")
    revision_env = _env_entries(revision)
    template = (service.get("spec") or {}).get("template") or {}
    template_env = _env_entries({"spec": template.get("spec")})
    revision_selector = _account_selector(revision_env, expected_service)
    template_selector = _account_selector(template_env, expected_service)
    if revision_selector != template_selector:
        raise ValueError("active_revision_binding_mismatch")
    _validate_diagnostic_gate(revision_env)
    _validate_diagnostic_gate(template_env)
    service_template_spec = ((service.get("spec") or {}).get("template") or {}).get("spec")
    if not isinstance(service_template_spec, dict):
        raise ValueError("service_template_spec_missing")
    name_mode = "strict"
    if _config_spec_hash(revision_spec) != _config_spec_hash(service_template_spec):
        difference_groups = _config_difference_groups(revision_spec, service_template_spec)
        desired_document = {
            "metadata": template.get("metadata"),
            "spec": service_template_spec,
        }
        if difference_groups == ["primary_name"] and _has_provider_default_primary_name(
            revision_spec,
            service_template_spec,
            serving_document=revision,
            desired_document=desired_document,
        ):
            name_mode = "provider_default"
            return name_mode
        groups = ",".join(difference_groups)
        details = ""
        if "primary_name" in difference_groups:
            details = ":primary_name_shape=" + json.dumps(
                _primary_name_shape_diagnostic(
                    revision_spec,
                    service_template_spec,
                    serving_document=revision,
                    desired_document=desired_document,
                ),
                sort_keys=True,
                separators=(",", ":"),
            )
        raise ValueError(f"active_revision_service_config_mismatch:{groups}{details}")
    return name_mode


def _traffic_hash(rows: list[dict[str, Any]]) -> str:
    canonical_rows = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":")))
    return _canonical_hash(canonical_rows)


def _validate_existing_diagnostic_tag(summary: dict[str, Any]) -> None:
    latest_created = summary["latest_created_revision"]
    matching_rows = [
        row
        for row in summary["all_traffic_rows"]
        if isinstance(row, dict) and row.get("tag") == DIAGNOSTIC_TAG
    ]
    if len(matching_rows) != 1:
        raise ValueError("diagnostic_tag_ambiguous")
    row = matching_rows[0]
    try:
        percent = int(row.get("percent", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("diagnostic_tag_invalid") from exc
    if percent != 0 or row.get("revisionName") != latest_created:
        raise ValueError("diagnostic_tag_revision_invalid")


def _validate_digest_image_ref(image_ref: str) -> None:
    if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image_ref):
        raise ValueError("existing_image_digest_invalid")


def _scheduler_jobs_hash(jobs: Any) -> str:
    if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
        raise ValueError("scheduler_shape_invalid")
    normalized = []
    for job in jobs:
        normalized.append(
            {key: value for key, value in job.items() if key not in OBSERVATION_ONLY_SCHEDULER_FIELDS}
        )
    normalized.sort(key=lambda row: str(row.get("name") or ""))
    return _canonical_hash(normalized)


def _read_service() -> dict[str, Any]:
    try:
        service = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("service_json_invalid") from exc
    if not isinstance(service, dict):
        raise ValueError("service_json_invalid")
    return service


def capture_baseline(
    state_path: Path,
    revision_path: Path,
    *,
    expected_service: str,
    expected_source_sha: str,
    expected_existing_image: str | None = None,
) -> None:
    service = _read_service()
    try:
        revision = json.loads(revision_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("active_revision_json_invalid") from exc
    if not isinstance(revision, dict):
        raise ValueError("active_revision_json_invalid")
    if expected_existing_image is not None:
        _validate_digest_image_ref(expected_existing_image)
    name_mode = _validate_serving_revision(
        service,
        revision,
        expected_service=expected_service,
        expected_source_sha=expected_source_sha,
    )
    if name_mode == "strict" and _has_provider_default_primary_name(
        revision["spec"],
        service["spec"]["template"]["spec"],
        serving_document=revision,
        desired_document=service["spec"]["template"],
    ):
        name_mode = "provider_default"
    summary = _service_summary(
        service, allow_diagnostic_tag=expected_existing_image is not None
    )
    if expected_existing_image is not None:
        _validate_existing_diagnostic_tag(summary)
        if summary["first_container_image"] != expected_existing_image:
            raise ValueError("existing_candidate_image_not_read_back")
    desired_positive_rows = [
        row for row in summary["desired_traffic_rows"] if int(row.get("percent", 0)) > 0
    ]
    if (
        len(desired_positive_rows) != 1
        or desired_positive_rows[0].get("revisionName") != summary["active_traffic"][0]["revisionName"]
    ):
        raise ValueError("desired_traffic_not_active_100")
    state = {
        "active_traffic_sha256": _traffic_hash(summary["traffic_rows"]),
        "desired_traffic_sha256": _canonical_hash(summary["desired_traffic_rows"]),
        "service_spec_sha256": _canonical_hash(
            _service_spec_for_name_mode(
                summary["service_spec_without_primary_image"],
                name_mode,
                document={
                    "metadata": service["spec"]["template"].get("metadata"),
                    "spec": service["spec"]["template"]["spec"],
                },
                name_source_spec=service["spec"]["template"]["spec"],
            )
        ),
        "primary_name_mode": name_mode,
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(state, stream, sort_keys=True, separators=(",", ":"))


def verify_readback(state_path: Path, expected_image: str) -> None:
    service = _read_service()
    summary = _service_summary(service, allow_diagnostic_tag=True)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("baseline_unavailable") from exc
    if not isinstance(state, dict):
        raise ValueError("baseline_invalid")
    if _traffic_hash(summary["traffic_rows"]) != state.get("active_traffic_sha256"):
        raise ValueError("active_traffic_changed")
    if _canonical_hash(summary["desired_traffic_rows"]) != state.get("desired_traffic_sha256"):
        raise ValueError("desired_traffic_changed")
    try:
        normalized_service_spec = _service_spec_for_name_mode(
            summary["service_spec_without_primary_image"],
            state.get("primary_name_mode"),
            document={
                "metadata": (service.get("spec") or {}).get("template", {}).get("metadata"),
                "spec": (service.get("spec") or {}).get("template", {}).get("spec"),
            },
            name_source_spec=(service.get("spec") or {}).get("template", {}).get("spec"),
        )
    except ValueError as exc:
        if str(exc) == "baseline_invalid":
            raise
        raise ValueError("service_configuration_changed") from exc
    if _canonical_hash(normalized_service_spec) != state.get("service_spec_sha256"):
        raise ValueError("service_configuration_changed")
    if summary["first_container_image"] != expected_image:
        raise ValueError("candidate_image_not_read_back")

    latest_created = summary["latest_created_revision"]
    matching_tag_rows = [
        row
        for row in summary["all_traffic_rows"]
        if isinstance(row, dict) and row.get("tag") == DIAGNOSTIC_TAG
    ]
    if len(matching_tag_rows) != 1:
        raise ValueError("diagnostic_tag_not_read_back")
    tag_row = matching_tag_rows[0]
    try:
        tag_percent = int(tag_row.get("percent", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("diagnostic_tag_invalid") from exc
    if tag_percent != 0 or tag_row.get("revisionName") != latest_created:
        raise ValueError("diagnostic_tag_revision_invalid")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase", choices=("active-revision", "capture", "verify", "scheduler-hash", "tag-budget")
    )
    parser.add_argument("--state", type=Path)
    parser.add_argument("--revision", type=Path)
    parser.add_argument("--expected-service")
    parser.add_argument("--expected-source-sha")
    parser.add_argument("--expected-existing-image")
    parser.add_argument("--expected-image")
    parser.add_argument("--tag")
    args = parser.parse_args()
    try:
        if args.phase == "active-revision":
            service = _read_service()
            active, _ = _active_traffic(service)
            print(active[0]["revisionName"])
            return 0
        if args.phase == "tag-budget":
            _validate_diagnostic_tag_budget(args.expected_service or "", args.tag or "")
            print("diagnostic_tag_budget_ok")
            return 0
        if args.phase == "scheduler-hash":
            jobs = json.load(sys.stdin)
            print(_scheduler_jobs_hash(jobs))
            return 0
        if args.state is None:
            raise ValueError("state_path_missing")
        if args.phase == "capture":
            if args.revision is None or not args.expected_service or not args.expected_source_sha:
                raise ValueError("serving_identity_inputs_missing")
            capture_baseline(
                args.state,
                args.revision,
                expected_service=args.expected_service,
                expected_source_sha=args.expected_source_sha,
                expected_existing_image=args.expected_existing_image,
            )
        else:
            if not args.expected_image:
                raise ValueError("expected_image_missing")
            verify_readback(args.state, args.expected_image)
    except ValueError as exc:
        print(f"cached_diagnostic_stage_blocked:{exc}", file=sys.stderr)
        return 1
    print("cached_diagnostic_stage_readback_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
