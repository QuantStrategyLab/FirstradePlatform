#!/usr/bin/env python3
"""Validate the facts-only Cloud Run configuration sync without exposing values."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping
from typing import Any


APPROVED_SYNC_URL = "https://qsl-strategy-switch-console.pigbibi.workers.dev/api/account-facts/sync"
FACTS_ENV_KEYS = (
    "FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED",
    "FIRSTRADE_ACCOUNT_FACTS_SYNC_URL",
    "FIRSTRADE_ACCOUNT_FACTS_TARGET_ID",
    "FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID",
    "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_KEY",
    "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE",
    "FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN",
)
_TARGET_ID = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\Z", re.ASCII)
_BINDING_ID = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_CONFIG_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)
_ACCOUNT_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z", re.ASCII)
_SHA = re.compile(r"[a-f0-9]{40}\Z", re.ASCII)
_RUN_ID = re.compile(r"[1-9][0-9]*\Z", re.ASCII)
_LATEST_TRAFFIC_TYPE = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
_SECRET_ANNOTATION = "run.googleapis.com/secrets"
_GCLOUD_CLIENT_ANNOTATIONS = {
    "run.googleapis.com/client-name",
    "run.googleapis.com/client-version",
}


class ConfigSyncError(ValueError):
    """Fixed safe failure from the facts-only configuration preflight/readback."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConfigSyncError("configuration_incomplete")
    return value


def desired_facts_config() -> dict[str, str]:
    enabled = _required("FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED")
    if enabled not in {"true", "false"}:
        raise ConfigSyncError("configuration_invalid")
    if _required("FIRSTRADE_ACCOUNT_FACTS_SYNC_URL") != APPROVED_SYNC_URL:
        raise ConfigSyncError("destination_not_approved")
    target_id = _required("FIRSTRADE_ACCOUNT_FACTS_TARGET_ID")
    binding_id = _required("FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID")
    account_key = _required("FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_KEY")
    account_scope = _required("FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE")
    secret_name = _required("FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN_SECRET_NAME")
    if not _TARGET_ID.fullmatch(target_id):
        raise ConfigSyncError("target_invalid")
    if not _BINDING_ID.fullmatch(binding_id):
        raise ConfigSyncError("source_binding_invalid")
    if not _CONFIG_VALUE.fullmatch(account_key):
        raise ConfigSyncError("account_key_invalid")
    if not _CONFIG_VALUE.fullmatch(account_scope):
        raise ConfigSyncError("account_scope_invalid")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", secret_name, re.ASCII):
        raise ConfigSyncError("secret_reference_invalid")
    return {
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED": enabled,
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_URL": APPROVED_SYNC_URL,
        "FIRSTRADE_ACCOUNT_FACTS_TARGET_ID": target_id,
        "FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID": binding_id,
        "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_KEY": account_key,
        "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE": account_scope,
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN_SECRET_NAME": secret_name,
    }


def _service_environment(service: Mapping[str, Any], service_name: str) -> dict[str, Mapping[str, Any]]:
    spec = service.get("spec")
    template = spec.get("template") if isinstance(spec, Mapping) else None
    template_spec = template.get("spec") if isinstance(template, Mapping) else None
    containers = template_spec.get("containers") if isinstance(template_spec, Mapping) else None
    if not isinstance(containers, list) or len(containers) != 1 or not isinstance(containers[0], Mapping):
        raise ConfigSyncError("service_shape_invalid")
    rows = containers[0].get("env") or []
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise ConfigSyncError("service_shape_invalid")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        name = row.get("name")
        if not isinstance(name, str) or not name or name in result:
            raise ConfigSyncError("service_environment_ambiguous")
        result[name] = row

    raw_target = result.get("RUNTIME_TARGET_JSON", {}).get("value")
    if not isinstance(raw_target, str) or not raw_target:
        raise ConfigSyncError("runtime_target_unavailable")
    try:
        target = json.loads(raw_target)
    except (TypeError, json.JSONDecodeError):
        raise ConfigSyncError("runtime_target_invalid") from None
    if not isinstance(target, Mapping) or str(target.get("platform_id") or "").lower() != "firstrade":
        raise ConfigSyncError("runtime_target_mismatch")
    configured_service = target.get("service_name")
    if configured_service and configured_service != service_name:
        raise ConfigSyncError("runtime_target_mismatch")
    selectors = target.get("account_selector")
    if isinstance(selectors, str):
        selectors = [selectors]
    if not isinstance(selectors, list) or len(selectors) != 1:
        raise ConfigSyncError("runtime_target_account_selector_invalid")
    selector = selectors[0]
    if not isinstance(selector, str) or not _ACCOUNT_ID.fullmatch(selector):
        raise ConfigSyncError("runtime_target_account_selector_invalid")
    legacy_account = result.get("FIRSTRADE_ACCOUNT", {}).get("value")
    if legacy_account and legacy_account != selector:
        raise ConfigSyncError("runtime_target_account_selector_invalid")
    if target.get("account_scope") != _required("FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE"):
        raise ConfigSyncError("runtime_target_scope_mismatch")
    return result


def _project_identity() -> tuple[str, str]:
    project_id = _required("GCP_PROJECT_ID")
    project_number = _required("GCP_PROJECT_NUMBER")
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project_id, re.ASCII):
        raise ConfigSyncError("project_identity_invalid")
    if not re.fullmatch(r"[0-9]{6,20}", project_number, re.ASCII):
        raise ConfigSyncError("project_identity_invalid")
    return project_id, project_number


def _secret_aliases(service: Mapping[str, Any]) -> dict[str, str]:
    spec = service.get("spec")
    template = spec.get("template") if isinstance(spec, Mapping) else None
    metadata = template.get("metadata") if isinstance(template, Mapping) else None
    annotations = metadata.get("annotations") if isinstance(metadata, Mapping) else None
    if not isinstance(annotations, Mapping) or _SECRET_ANNOTATION not in annotations:
        return {}
    raw = annotations.get(_SECRET_ANNOTATION)
    if not isinstance(raw, str):
        raise ConfigSyncError("secret_alias_annotation_invalid")
    aliases: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            raise ConfigSyncError("secret_alias_annotation_invalid")
        alias, separator, resource = entry.partition(":")
        if (
            not separator
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", alias, re.ASCII)
            or not re.fullmatch(r"projects/[0-9]{6,20}/secrets/[A-Za-z0-9_-]{1,255}", resource, re.ASCII)
            or alias in aliases
        ):
            raise ConfigSyncError("secret_alias_annotation_invalid")
        aliases[alias] = resource
    return aliases


def _secret_alias_usage(service: Mapping[str, Any], alias: str) -> tuple[int, int]:
    spec = service.get("spec")
    template = spec.get("template") if isinstance(spec, Mapping) else None
    template_spec = template.get("spec") if isinstance(template, Mapping) else None
    containers = template_spec.get("containers") if isinstance(template_spec, Mapping) else None
    if not isinstance(containers, list) or len(containers) != 1 or not isinstance(containers[0], Mapping):
        raise ConfigSyncError("service_shape_invalid")
    token_references = 0
    other_references = 0
    rows = containers[0].get("env") or []
    if not isinstance(rows, list):
        raise ConfigSyncError("service_shape_invalid")
    for row in rows:
        if not isinstance(row, Mapping):
            raise ConfigSyncError("service_shape_invalid")
        value_from = row.get("valueFrom")
        secret_ref = value_from.get("secretKeyRef") if isinstance(value_from, Mapping) else None
        if isinstance(secret_ref, Mapping) and secret_ref.get("name") == alias:
            if row.get("name") == "FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN":
                token_references += 1
            else:
                other_references += 1
    volumes = template_spec.get("volumes") or []
    if not isinstance(volumes, list):
        raise ConfigSyncError("service_shape_invalid")
    for volume in volumes:
        if not isinstance(volume, Mapping):
            raise ConfigSyncError("service_shape_invalid")
        secret = volume.get("secret")
        if isinstance(secret, Mapping) and secret.get("secretName") == alias:
            other_references += 1
    return token_references, other_references


def _baseline_secret_aliases_to_ignore(
    service: Mapping[str, Any],
    environment: Mapping[str, Mapping[str, Any]],
    desired_secret_name: str,
    project_number: str,
) -> set[str]:
    aliases = _secret_aliases(service)
    ignored: set[str] = set()
    token_row = environment.get("FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN")
    value_from = token_row.get("valueFrom") if isinstance(token_row, Mapping) else None
    secret_ref = value_from.get("secretKeyRef") if isinstance(value_from, Mapping) else None
    current_alias = secret_ref.get("name") if isinstance(secret_ref, Mapping) else None
    if isinstance(current_alias, str) and current_alias in aliases:
        token_uses, other_uses = _secret_alias_usage(service, current_alias)
        if token_uses == 1 and other_uses == 0:
            ignored.add(current_alias)

    expected_resource = f"projects/{project_number}/secrets/{desired_secret_name}"
    prospective = [
        alias
        for alias, resource in aliases.items()
        if resource == expected_resource and alias not in ignored
        and _secret_alias_usage(service, alias) == (0, 0)
    ]
    if len(prospective) == 1:
        ignored.add(prospective[0])
    return ignored


def _current_facts_token_alias(
    service: Mapping[str, Any],
    environment: Mapping[str, Mapping[str, Any]],
    expected_secret_name: str,
    project_number: str,
) -> str | None:
    token_row = environment.get("FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN")
    value_from = token_row.get("valueFrom") if isinstance(token_row, Mapping) else None
    secret_ref = value_from.get("secretKeyRef") if isinstance(value_from, Mapping) else None
    expected_resource = f"projects/{project_number}/secrets/{expected_secret_name}"
    if (
        not isinstance(secret_ref, Mapping)
        or secret_ref.get("key") != "latest"
        or "value" in token_row
    ):
        raise ConfigSyncError("facts_secret_reference_readback_mismatch")
    reference_name = secret_ref.get("name")
    if not isinstance(reference_name, str):
        raise ConfigSyncError("facts_secret_reference_readback_mismatch")
    aliases = _secret_aliases(service)
    if reference_name in aliases:
        if aliases[reference_name] != expected_resource:
            raise ConfigSyncError("facts_secret_reference_readback_mismatch")
        token_uses, other_uses = _secret_alias_usage(service, reference_name)
        if token_uses != 1 or other_uses != 0:
            raise ConfigSyncError("facts_secret_alias_shared")
        return reference_name
    if reference_name == expected_secret_name:
        return None
    raise ConfigSyncError("facts_secret_reference_readback_mismatch")


def _canonical_traffic_rows(
    rows: Any,
    *,
    latest_revision: str | None = None,
    allow_latest: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise ConfigSyncError("traffic_readback_unavailable")
    canonical_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ConfigSyncError("traffic_readback_invalid")
        target = dict(row)
        is_latest = target.get("latestRevision") is True or target.get("type") == _LATEST_TRAFFIC_TYPE
        revision_name = target.get("revisionName")
        if is_latest:
            if not allow_latest or not latest_revision:
                raise ConfigSyncError("traffic_readback_invalid")
            if revision_name not in (None, latest_revision):
                raise ConfigSyncError("traffic_readback_invalid")
            revision_name = latest_revision
        if not isinstance(revision_name, str) or not revision_name:
            raise ConfigSyncError("traffic_readback_invalid")
        canonical_target: dict[str, Any] = {"revisionName": revision_name}
        if "percent" in target:
            percent = target["percent"]
            if percent is not None and (
                isinstance(percent, bool)
                or not isinstance(percent, (int, float))
                or percent < 0
                or percent > 100
            ):
                raise ConfigSyncError("traffic_readback_invalid")
            canonical_target["percent"] = percent
        if target.get("tag") is not None:
            if not isinstance(target["tag"], str) or not target["tag"]:
                raise ConfigSyncError("traffic_readback_invalid")
            canonical_target["tag"] = target["tag"]
        canonical_rows.append(canonical_target)
    return sorted(canonical_rows, key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":")))


def _spec_traffic_digest(service: Mapping[str, Any], baseline_latest_revision: str | None = None) -> str:
    spec = service.get("spec")
    rows = spec.get("traffic") if isinstance(spec, Mapping) else None
    normalized = _canonical_traffic_rows(
        rows,
        latest_revision=baseline_latest_revision,
        allow_latest=baseline_latest_revision is not None,
    )
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _status_traffic_digest(service: Mapping[str, Any]) -> str:
    status = service.get("status")
    rows = status.get("traffic") if isinstance(status, Mapping) else None
    normalized = _canonical_traffic_rows(rows)
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _template_digest(service: Mapping[str, Any], ignored_secret_aliases: set[str] | None = None) -> str:
    spec = service.get("spec")
    if not isinstance(spec, Mapping):
        raise ConfigSyncError("service_shape_invalid")
    normalized = deepcopy(dict(spec))
    normalized.pop("traffic", None)
    template = normalized.get("template")
    if not isinstance(template, dict):
        raise ConfigSyncError("service_shape_invalid")
    metadata = template.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict):
            raise ConfigSyncError("service_shape_invalid")
        metadata.pop("name", None)
        labels = metadata.get("labels")
        if labels is not None:
            if not isinstance(labels, dict):
                raise ConfigSyncError("service_shape_invalid")
            labels.pop("client.knative.dev/nonce", None)
            if not labels:
                metadata.pop("labels", None)
        annotations = metadata.get("annotations")
        if annotations is not None:
            if not isinstance(annotations, dict):
                raise ConfigSyncError("service_shape_invalid")
            for key in _GCLOUD_CLIENT_ANNOTATIONS:
                annotations.pop(key, None)
            if _SECRET_ANNOTATION in annotations:
                aliases = _secret_aliases({"spec": {"template": template}})
                for alias in ignored_secret_aliases or set():
                    aliases.pop(alias, None)
                if aliases:
                    annotations[_SECRET_ANNOTATION] = ",".join(
                        f"{alias}:{resource}" for alias, resource in sorted(aliases.items())
                    )
                else:
                    annotations.pop(_SECRET_ANNOTATION, None)
            if not annotations:
                metadata.pop("annotations", None)
        if not metadata:
            template.pop("metadata", None)
    template_spec = template.get("spec")
    containers = template_spec.get("containers") if isinstance(template_spec, dict) else None
    if not isinstance(containers, list) or len(containers) != 1 or not isinstance(containers[0], dict):
        raise ConfigSyncError("service_shape_invalid")
    env_rows = containers[0].get("env") or []
    if not isinstance(env_rows, list):
        raise ConfigSyncError("service_shape_invalid")
    containers[0]["env"] = sorted(
        [row for row in env_rows if isinstance(row, Mapping) and row.get("name") not in FACTS_ENV_KEYS],
        key=lambda row: str(row.get("name") or ""),
    )
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _revision_names(service: Mapping[str, Any]) -> tuple[str, str]:
    status = service.get("status")
    if not isinstance(status, Mapping):
        raise ConfigSyncError("revision_metadata_unavailable")
    latest_created = status.get("latestCreatedRevisionName")
    latest_ready = status.get("latestReadyRevisionName")
    if any(
        not isinstance(name, str) or re.fullmatch(r"[a-z][a-z0-9-]{0,62}", name, re.ASCII) is None
        for name in (latest_created, latest_ready)
    ):
        raise ConfigSyncError("revision_metadata_unavailable")
    # A zero-traffic candidate can differ from the service's serving alias.
    # The workflow separately checks this exact created revision's Ready/SHA.
    return latest_created, latest_ready


def verify_revision(revision: Mapping[str, Any], expected_sha: str) -> None:
    if not _SHA.fullmatch(expected_sha):
        raise ConfigSyncError("expected_sha_invalid")
    metadata = revision.get("metadata")
    labels = metadata.get("labels") if isinstance(metadata, Mapping) else None
    if not isinstance(labels, Mapping):
        raise ConfigSyncError("revision_metadata_unavailable")
    if labels.get("commit-sha") != expected_sha or not _RUN_ID.fullmatch(str(labels.get("github-run-id") or "")):
        raise ConfigSyncError("revision_source_mismatch")
    status = revision.get("status")
    conditions = status.get("conditions") if isinstance(status, Mapping) else None
    ready = any(
        isinstance(row, Mapping) and row.get("type") == "Ready" and row.get("status") == "True"
        for row in conditions or []
    ) if isinstance(conditions, list) else False
    if not ready:
        raise ConfigSyncError("latest_revision_not_ready")


def verify_before(service: Mapping[str, Any], service_name: str) -> dict[str, str]:
    environment = _service_environment(service, service_name)
    desired = desired_facts_config()
    _project_id, project_number = _project_identity()
    latest_revision, _ready_revision = _revision_names(service)
    ignored_secret_aliases = _baseline_secret_aliases_to_ignore(
        service,
        environment,
        desired["FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN_SECRET_NAME"],
        project_number,
    )
    return {
        "template_sha256": _template_digest(service, ignored_secret_aliases),
        "spec_traffic_sha256": _spec_traffic_digest(service, latest_revision),
        "traffic_sha256": _status_traffic_digest(service),
    }


def verify_after(
    service: Mapping[str, Any],
    service_name: str,
    desired: Mapping[str, str],
    secret_name: str,
    baseline: Mapping[str, str],
) -> None:
    environment = _service_environment(service, service_name)
    current_latest_revision, _ready_revision = _revision_names(service)
    _project_id, project_number = _project_identity()
    token_alias = _current_facts_token_alias(
        service,
        environment,
        secret_name,
        project_number,
    )
    facts_fields = FACTS_ENV_KEYS[:-1]
    for name in facts_fields:
        row = environment.get(name)
        if not isinstance(row, Mapping) or row.get("value") != desired.get(name) or "valueFrom" in row:
            raise ConfigSyncError("facts_configuration_readback_mismatch")
    ignored_secret_aliases = {token_alias} if token_alias else set()
    if _template_digest(service, ignored_secret_aliases) != baseline.get("template_sha256"):
        raise ConfigSyncError("unrelated_service_configuration_changed")
    if _spec_traffic_digest(service, current_latest_revision) != baseline.get("spec_traffic_sha256"):
        raise ConfigSyncError("traffic_changed")
    if _status_traffic_digest(service) != baseline.get("traffic_sha256"):
        raise ConfigSyncError("traffic_changed")


def _load_stdin() -> Mapping[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        raise ConfigSyncError("service_readback_invalid") from None
    if not isinstance(payload, Mapping):
        raise ConfigSyncError("service_readback_invalid")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("config", "before", "revision", "after"))
    parser.add_argument("--service", default=os.environ.get("CLOUD_RUN_SERVICE", ""))
    parser.add_argument("--expected-sha", default=os.environ.get("EXPECTED_SHA", ""))
    args = parser.parse_args()
    try:
        if args.mode == "config":
            desired_facts_config()
            print("account_facts_config_validated=true")
            return 0
        payload = _load_stdin()
        if not args.service:
            raise ConfigSyncError("service_target_unavailable")
        if args.mode == "revision":
            verify_revision(payload, args.expected_sha)
            print("revision_source_verified=true")
        elif args.mode == "before":
            print(json.dumps(verify_before(payload, args.service), separators=(",", ":")))
        else:
            desired = desired_facts_config()
            baseline = json.loads(os.environ.get("ACCOUNT_FACTS_BASELINE_JSON", "{}"))
            verify_after(
                payload,
                args.service,
                desired,
                desired["FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN_SECRET_NAME"],
                baseline,
            )
            print("account_facts_config_readback_verified=true")
    except (ConfigSyncError, TypeError, ValueError, KeyError) as exc:
        reason = exc.reason if isinstance(exc, ConfigSyncError) else "readback_invalid"
        print(f"account_facts_config_sync_failed={reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
