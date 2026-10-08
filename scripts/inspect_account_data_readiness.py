"""Read Cloud Run metadata for the configured Firstrade serving revision."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

PROJECT_ID = "firstradequant"
API_ROOT = "https://run.googleapis.com/v2"
SERVICE_NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
REGION_PATH_PATTERN = r"[a-z]+(?:-[a-z0-9]+)+[0-9]"
REGION_RE = re.compile(REGION_PATH_PATTERN + r"\Z")
COMMIT_RE = re.compile(r"[0-9a-fA-F]{40}\Z")
MAX_RESPONSE_BYTES = 2_000_000
ACCOUNT_FACTS_DESTINATION = "https://qsl-strategy-switch-console.pigbibi.workers.dev/api/account-facts/sync"


class DiagnosticFailure(Exception):
    def __init__(self, reason: str, http_status: int | None = None) -> None:
        self.reason = reason
        self.http_status = http_status


def _safe_resource_part(value: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise DiagnosticFailure("invalid_target_configuration")
    return value


def _service_path(region: str, service: str) -> str:
    return (
        f"projects/{PROJECT_ID}/locations/{region}/services/{service}"
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise DiagnosticFailure("redirect_rejected", code)


def _request_json(url: str, token: str) -> Mapping[str, Any]:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "run.googleapis.com"
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(
            rf"/v2/projects/{PROJECT_ID}/locations/{REGION_PATH_PATTERN}"
            r"/services/[a-z][a-z0-9-]{0,62}(?:/revisions/[a-z][a-z0-9-]{0,62})?",
            parsed.path,
        )
    ):
        raise DiagnosticFailure("request_target_rejected")

    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=15) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise DiagnosticFailure("metadata_http_error", int(exc.code)) from None
    except DiagnosticFailure:
        raise
    except (urllib.error.URLError, TimeoutError, OSError):
        raise DiagnosticFailure("metadata_transport_error") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise DiagnosticFailure("metadata_response_too_large")

    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise DiagnosticFailure("metadata_response_invalid") from None
    if not isinstance(payload, Mapping):
        raise DiagnosticFailure("metadata_response_invalid")
    return payload


def _ready(document: Mapping[str, Any]) -> bool:
    if document.get("reconciling", False) is not False:
        return False
    generation = document.get("generation")
    observed_generation = document.get("observedGeneration")
    if not isinstance(generation, str) or not generation or generation != observed_generation:
        return False
    conditions = document.get("conditions")
    if not isinstance(conditions, list):
        return False
    ready = [
        condition
        for condition in conditions
        if isinstance(condition, Mapping) and condition.get("type") == "Ready"
    ]
    return len(ready) == 1 and ready[0].get("state") == "CONDITION_SUCCEEDED"


def _service_ready(service: Mapping[str, Any]) -> bool:
    if service.get("reconciling", False) is not False:
        return False
    generation = service.get("generation")
    observed_generation = service.get("observedGeneration")
    if not isinstance(generation, str) or not generation or generation != observed_generation:
        return False
    terminal = service.get("terminalCondition")
    return isinstance(terminal, Mapping) and terminal.get("state") == "CONDITION_SUCCEEDED"


def _service_fingerprint(service: Mapping[str, Any]) -> str:
    selected = {
        "etag": service.get("etag"),
        "template": service.get("template"),
        "trafficStatuses": service.get("trafficStatuses"),
        "reconciling": service.get("reconciling"),
        "generation": service.get("generation"),
        "observedGeneration": service.get("observedGeneration"),
        "terminalCondition": service.get("terminalCondition"),
        "conditions": service.get("conditions"),
    }
    try:
        encoded = json.dumps(selected, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        raise DiagnosticFailure("metadata_response_invalid") from None
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _serving_revision(service: Mapping[str, Any], service_path: str) -> str:
    traffic = service.get("trafficStatuses")
    if not isinstance(traffic, list) or not traffic:
        raise DiagnosticFailure("serving_traffic_unavailable")
    total = 0
    positive: list[str] = []
    for entry in traffic:
        if not isinstance(entry, Mapping):
            raise DiagnosticFailure("serving_traffic_invalid")
        percent = entry.get("percent", 0)
        revision = entry.get("revision")
        if isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 100:
            raise DiagnosticFailure("serving_traffic_invalid")
        if not isinstance(revision, str) or not revision:
            raise DiagnosticFailure("serving_traffic_invalid")
        prefix = f"{service_path}/revisions/"
        if revision.startswith(prefix):
            revision = revision.removeprefix(prefix)
        elif "/" in revision:
            raise DiagnosticFailure("serving_traffic_invalid")
        if not SERVICE_NAME_RE.fullmatch(revision):
            raise DiagnosticFailure("serving_traffic_invalid")
        total += percent
        if percent > 0:
            positive.append(revision)
    if total != 100 or len(positive) != 1:
        raise DiagnosticFailure("serving_traffic_ambiguous")
    return positive[0]


def _selector_configuration(revision: Mapping[str, Any]) -> tuple[bool, str]:
    containers = revision.get("containers")
    if not isinstance(containers, list) or len(containers) != 1:
        raise DiagnosticFailure("revision_container_invalid")
    env = containers[0].get("env") if isinstance(containers[0], Mapping) else None
    if env is None:
        env = []
    if not isinstance(env, list):
        raise DiagnosticFailure("revision_environment_invalid")
    matches = [
        entry
        for entry in env
        if isinstance(entry, Mapping) and entry.get("name") == "FIRSTRADE_ACCOUNT"
    ]
    if len(matches) > 1:
        raise DiagnosticFailure("selector_configuration_ambiguous")
    if not matches:
        return False, "absent"
    entry = matches[0]
    value_source = entry.get("valueSource")
    if isinstance(value_source, Mapping) and isinstance(
        value_source.get("secretKeyRef"), Mapping
    ):
        return True, "secret_reference"
    if isinstance(entry.get("value"), str) and entry["value"].strip():
        return True, "literal"
    return False, "absent"


def _unique_env_entry(env: list[Any], name: str) -> Mapping[str, Any] | None:
    matches = [
        entry
        for entry in env
        if isinstance(entry, Mapping) and entry.get("name") == name
    ]
    if len(matches) > 1:
        raise DiagnosticFailure("runtime_target_configuration_ambiguous")
    return matches[0] if matches else None


def _literal_env_value(entry: Mapping[str, Any]) -> str | None:
    value_source = entry.get("valueSource")
    secret_ref = (
        value_source.get("secretKeyRef")
        if isinstance(value_source, Mapping)
        else None
    )
    has_secret_ref = isinstance(secret_ref, Mapping)
    has_literal = "value" in entry
    if has_secret_ref and has_literal:
        raise DiagnosticFailure("runtime_target_configuration_ambiguous")
    if has_secret_ref:
        return None
    value = entry.get("value")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise DiagnosticFailure("runtime_target_configuration_invalid")
    return value


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DiagnosticFailure("runtime_target_json_ambiguous")
        result[key] = value
    return result


def _selector_is_nonempty(value: Any) -> bool:
    if value is None:
        return False
    try:
        if isinstance(value, str):
            return bool(value.strip())
        return any(item is not None and str(item).strip() for item in value)
    except TypeError:
        raise DiagnosticFailure("runtime_target_selector_invalid") from None


def _effective_selector_configuration(
    revision: Mapping[str, Any], direct_selector: tuple[bool, str]
) -> tuple[bool, str]:
    containers = revision.get("containers")
    if not isinstance(containers, list) or len(containers) != 1:
        raise DiagnosticFailure("revision_container_invalid")
    container = containers[0]
    env = container.get("env") if isinstance(container, Mapping) else None
    if env is None:
        env = []
    if not isinstance(env, list):
        raise DiagnosticFailure("revision_environment_invalid")

    # The deployed resolver prefers QSL_RUNTIME_TARGET_JSON, then
    # RUNTIME_TARGET_JSON, and only uses FIRSTRADE_ACCOUNT when both are empty.
    for name, source in (
        ("QSL_RUNTIME_TARGET_JSON", "qsl_runtime_target_literal"),
        ("RUNTIME_TARGET_JSON", "runtime_target_literal"),
    ):
        entry = _unique_env_entry(env, name)
        if entry is None:
            continue
        raw = _literal_env_value(entry)
        if raw is None:
            return False, "runtime_target_secret_unresolved"
        if raw == "":
            continue
        if not raw.strip():
            raise DiagnosticFailure("runtime_target_json_invalid")
        try:
            payload = json.loads(raw, object_pairs_hook=_reject_duplicate_json_keys)
        except DiagnosticFailure:
            raise
        except (json.JSONDecodeError, TypeError, ValueError):
            raise DiagnosticFailure("runtime_target_json_invalid") from None
        if not isinstance(payload, Mapping):
            raise DiagnosticFailure("runtime_target_json_invalid")
        return _selector_is_nonempty(payload.get("account_selector")), source

    direct_configured, direct_source = direct_selector
    if direct_source == "secret_reference":
        return False, "first_trade_account_secret_unresolved"
    return direct_configured, f"first_trade_account_{direct_source}"


def _account_data_configuration(revision: Mapping[str, Any]) -> dict[str, Any]:
    """Describe serving settings, without resolving secrets or claiming session health."""
    containers = revision.get("containers")
    if not isinstance(containers, list) or len(containers) != 1:
        raise DiagnosticFailure("revision_container_invalid")
    env = containers[0].get("env", []) if isinstance(containers[0], Mapping) else None
    if not isinstance(env, list):
        raise DiagnosticFailure("revision_environment_invalid")

    def entry(name: str) -> Mapping[str, Any] | None:
        matches = [row for row in env if isinstance(row, Mapping) and row.get("name") == name]
        if len(matches) > 1:
            raise DiagnosticFailure("account_data_configuration_ambiguous")
        return matches[0] if matches else None

    def source(row: Mapping[str, Any] | None) -> str:
        if row is None:
            return "absent"
        ref = (row.get("valueSource") or {}).get("secretKeyRef") if isinstance(row.get("valueSource"), Mapping) else None
        if isinstance(ref, Mapping):
            if "value" in row:
                raise DiagnosticFailure("account_data_configuration_ambiguous")
            return "secret_reference" if isinstance(ref.get("secret"), str) and ref["secret"].strip() else "absent"
        value = row.get("value")
        return "literal" if isinstance(value, str) and value.strip() else "absent"

    def flag(name: str) -> bool | None:
        row = entry(name)
        kind = source(row)
        if kind == "absent":
            return False
        if kind == "secret_reference":
            return None
        value = row["value"].strip().lower()
        return value == "true" if value in {"true", "false"} else None

    destination = entry("FIRSTRADE_ACCOUNT_FACTS_SYNC_URL")
    destination_kind = source(destination)
    binding_sources = [source(entry(name)) for name in (
        "FIRSTRADE_ACCOUNT_FACTS_TARGET_ID", "FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID",
        "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_KEY", "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE",
    )]
    return {
        "sync_enabled": flag("FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED"),
        "approved_destination_configured": (
            destination.get("value") == ACCOUNT_FACTS_DESTINATION
            if destination_kind == "literal" else None if destination_kind == "secret_reference" else False
        ),
        "binding_settings_configured": all(kind != "absent" for kind in binding_sources),
        "sync_token_source": source(entry("FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN")),
        "session_reuse_enabled": flag("FIRSTRADE_REUSE_SESSION"),
        "session_cache_persistence_enabled": flag("FIRSTRADE_PERSIST_SESSION_CACHE"),
        "session_cache_bucket_configured": source(entry("FIRSTRADE_GCS_STATE_BUCKET")) != "absent",
        "account_binding_validity": "not_checked",
        "cached_session_validity": "not_checked",
    }


def inspect_readiness(
    service_name: str,
    region: str,
    token: str,
    *,
    request_json: Callable[[str, str], Mapping[str, Any]] = _request_json,
) -> dict[str, Any]:
    service_name = _safe_resource_part(service_name, SERVICE_NAME_RE)
    region = _safe_resource_part(region, REGION_RE)
    if not token:
        raise DiagnosticFailure("authentication_unavailable")
    service_path = _service_path(region, service_name)
    service_url = f"{API_ROOT}/{service_path}"
    before = request_json(service_url, token)
    if before.get("name") != service_path:
        raise DiagnosticFailure("service_identity_mismatch")
    if not _service_ready(before):
        raise DiagnosticFailure("service_not_ready")
    if not before.get("etag"):
        raise DiagnosticFailure("service_version_unavailable")
    revision_name = _serving_revision(before, service_path)
    revision_path = f"{service_path}/revisions/{urllib.parse.quote(revision_name, safe='-') }"
    revision = request_json(f"{API_ROOT}/{revision_path}", token)
    expected_revision_path = f"{service_path}/revisions/{revision_name}"
    if revision.get("name") != expected_revision_path:
        raise DiagnosticFailure("revision_identity_mismatch")
    if not _ready(revision):
        raise DiagnosticFailure("revision_not_ready")
    source_commit = (revision.get("labels") or {}).get("commit-sha")
    if not isinstance(source_commit, str) or not COMMIT_RE.fullmatch(source_commit):
        raise DiagnosticFailure("source_commit_unavailable")
    selector_configured, selector_source = _selector_configuration(revision)
    selector_nonempty, effective_selector_source = _effective_selector_configuration(
        revision, (selector_configured, selector_source)
    )
    account_data_configuration = _account_data_configuration(revision)
    after = request_json(service_url, token)
    if after.get("name") != service_path:
        raise DiagnosticFailure("service_identity_mismatch")
    if _service_fingerprint(before) != _service_fingerprint(after):
        raise DiagnosticFailure("service_changed_during_read")
    return {
        "status": "verified",
        "reason": "metadata_only_verified",
        "http_status": None,
        "source_commit": source_commit.lower(),
        "selector_configured": selector_configured,
        "selector_source": selector_source,
        "selector_nonempty": selector_nonempty,
        "effective_selector_source": effective_selector_source,
        "account_data_configuration": account_data_configuration,
    }


def _access_token() -> str:
    try:
        result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise DiagnosticFailure("authentication_unavailable") from None
    if result.returncode != 0 or not result.stdout.strip():
        raise DiagnosticFailure("authentication_unavailable")
    return result.stdout.strip()


def main() -> int:
    try:
        result = inspect_readiness(
            os.environ.get("CLOUD_RUN_SERVICE", ""),
            os.environ.get("CLOUD_RUN_REGION", ""),
            _access_token(),
        )
        exit_code = 0
    except DiagnosticFailure as exc:
        result = {
            "status": "blocked",
            "reason": exc.reason,
            "http_status": exc.http_status,
        }
        exit_code = 1
    except Exception:  # noqa: BLE001 - keep unexpected provider errors out of logs
        result = {"status": "blocked", "reason": "diagnostic_failed", "http_status": None}
        exit_code = 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
