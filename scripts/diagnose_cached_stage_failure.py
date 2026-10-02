"""Summarize private Cloud Run staging evidence without printing source values."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.verify_cached_diagnostic_stage import (
    DIAGNOSTIC_TAG,
    MAX_SERVICE_AND_TRAFFIC_TAG_LENGTH,
)

REASONS = (
    "permission",
    "act_as",
    "invalid_name",
    "image",
    "port",
    "startup",
    "missing_secret",
    "update_mask",
    "environment",
    "network",
    "quota",
    "traffic_tag_length",
    "traffic_tag_format",
    "traffic_tag_conflict",
    "traffic_tag_url_disabled",
)
_REASON_PATTERNS = {
    "act_as": re.compile(r"iam\.serviceaccounts\.actas|actas|service account user", re.I),
    "missing_secret": re.compile(r"secret[^\n]*(not found|does not exist|missing)|secretkeyref", re.I),
    "invalid_name": re.compile(
        r"invalid[^\n]*name|name[^\n]*invalid|must (start|end) with|"
        r"traffic[^\n]*tag[^\n]*too long|combined traffic tag and service name cannot exceed 46",
        re.I,
    ),
    "image": re.compile(
        r"image[^\n]*(pull|manifest|digest|not found|resolve)|failed to (resolve|pull|fetch)[^\n]*image",
        re.I,
    ),
    "port": re.compile(r"port[^\n]*(listen|start|bind|set)|listen on the port", re.I),
    "startup": re.compile(r"startup probe|container failed to start|startup[^\n]*failed", re.I),
    "permission": re.compile(r"permission denied|permission_denied|not authorized|forbidden|\b403\b", re.I),
    "update_mask": re.compile(r"update[ _-]?mask|field mask", re.I),
    "environment": re.compile(r"environment variable|environment configuration|env var", re.I),
    "network": re.compile(r"\bvpc\b|network|subnet|connector", re.I),
    "quota": re.compile(r"quota|resource[_ ]exhausted|limit exceeded", re.I),
    "traffic_tag_length": re.compile(r"traffic[^\n]*tags?[^\n]*(length|longer|shorter|characters|too long|at most|at least)", re.I),
    "traffic_tag_format": re.compile(r"traffic[^\n]*tags?[^\n]*(format|lowercase|dns|regex|valid)", re.I),
    "traffic_tag_conflict": re.compile(r"traffic[^\n]*tags?[^\n]*(reserved|already|duplicate|unique)", re.I),
    "traffic_tag_url_disabled": re.compile(r"traffic[^\n]*tags?[^\n]*(url[^\n]*disabled|disabled[^\n]*url|unsupported|not supported)", re.I),
}
_KNOWN_RPC_STATUS_CODES = frozenset(range(17))
_AUDIT_MESSAGE_TERMS = (
    "container",
    "name",
    "image",
    "traffic",
    "tag",
    "revision",
    "env",
    "secret",
    "update_mask",
    "port",
    "vpc",
    "quota",
    "immutable",
)
_AUDIT_MESSAGE_TERM_PATTERNS = {
    "container": re.compile(r"\bcontainers?\b", re.I),
    "name": re.compile(r"\bname\b", re.I),
    "image": re.compile(r"\bimages?\b", re.I),
    "traffic": re.compile(r"\btraffic\b", re.I),
    "tag": re.compile(r"\btags?\b", re.I),
    "revision": re.compile(r"\brevisions?\b", re.I),
    "env": re.compile(r"\benv(?:ironment)?\b", re.I),
    "secret": re.compile(r"\bsecret(?:keyref)?\b", re.I),
    "update_mask": _REASON_PATTERNS["update_mask"],
    "port": re.compile(r"\bports?\b", re.I),
    "vpc": re.compile(r"\bvpc\b", re.I),
    "quota": _REASON_PATTERNS["quota"],
    "immutable": re.compile(r"\bimmutab(?:le|ility)\b", re.I),
}
_COMBINED_TAG_NAME_LIMIT = re.compile(
    r"traffic[^\n]*tag[^\n]*too long|combined traffic tag and service name cannot exceed 46",
    re.I,
)


def _read_json(path: str | None) -> Any:
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _ready(resource: Any) -> bool | None:
    status = resource.get("status") if isinstance(resource, dict) else None
    conditions = status.get("conditions") if isinstance(status, dict) else None
    if not isinstance(conditions, list):
        return None
    ready = [item for item in conditions if isinstance(item, dict) and item.get("type") == "Ready"]
    if len(ready) != 1:
        return None
    state = ready[0].get("state", ready[0].get("status"))
    if state is True or state == "True" or state == "CONDITION_SUCCEEDED":
        return True
    if state is False or state == "False" or state == "CONDITION_FAILED":
        return False
    return None


def _classify(texts: list[str]) -> list[str]:
    joined = "\n".join(texts)
    return [reason for reason in REASONS if _REASON_PATTERNS[reason].search(joined)] or ["unknown"]


def _has_combined_tag_name_limit(texts: list[str]) -> bool:
    return bool(_COMBINED_TAG_NAME_LIMIT.search("\n".join(texts)))


def _revision_for_service(service: Any, revisions: Any) -> dict[str, Any] | None:
    if not isinstance(service, dict) or not isinstance(revisions, list):
        return None
    status = service.get("status")
    latest_name = status.get("latestCreatedRevisionName") if isinstance(status, dict) else None
    if not isinstance(latest_name, str) or not latest_name:
        return None
    matches = [
        item
        for item in revisions
        if isinstance(item, dict)
        and isinstance(item.get("metadata"), dict)
        and item["metadata"].get("name") == latest_name
    ]
    return matches[0] if len(matches) == 1 else None


def _condition_error_texts(resource: Any) -> list[str]:
    status = resource.get("status") if isinstance(resource, dict) else None
    conditions = status.get("conditions") if isinstance(status, dict) else None
    if not isinstance(conditions, list):
        return []
    texts = []
    for condition in conditions:
        if not isinstance(condition, dict):
            continue
        state = condition.get("state", condition.get("status"))
        if state not in ("CONDITION_FAILED", "False", False):
            continue
        for key in ("reason", "message"):
            value = condition.get(key)
            if isinstance(value, str):
                texts.append(value)
    return texts


def _condition_error_status_codes(resource: Any) -> list[int]:
    status = resource.get("status") if isinstance(resource, dict) else None
    conditions = status.get("conditions") if isinstance(status, dict) else None
    if not isinstance(conditions, list):
        return []
    codes = set()
    for condition in conditions:
        if not isinstance(condition, dict):
            continue
        state = condition.get("state", condition.get("status"))
        if state not in ("CONDITION_FAILED", "False", False):
            continue
        code = condition.get("code")
        if isinstance(code, int) and not isinstance(code, bool) and code in _KNOWN_RPC_STATUS_CODES:
            codes.add(code)
    return sorted(codes)


def _audit_identity_matches(
    entry: dict[str, Any], *, project: str, region: str, service: str
) -> bool:
    payload = entry.get("protoPayload")
    payload = payload if isinstance(payload, dict) else {}
    resource_name = payload.get("resourceName")
    labels = entry.get("resource")
    labels = labels.get("labels") if isinstance(labels, dict) else None
    exact_labels = (
        isinstance(labels, dict)
        and labels.get("project_id") == project
        and labels.get("location") == region
        and labels.get("service_name") == service
    )
    v2_name = f"projects/{project}/locations/{region}/services/{service}"
    v1_name = f"namespaces/{project}/services/{service}"
    if resource_name is not None:
        if not isinstance(resource_name, str):
            return False
        if resource_name == v2_name:
            return True
        return resource_name == v1_name and exact_labels
    return exact_labels


def _audit_error_texts(
    entries: Any,
    *,
    project: str,
    region: str,
    service: str,
    window_start: str,
    window_end: str,
) -> tuple[int | None, int | None, list[str], list[int] | None]:
    if not isinstance(entries, list):
        return None, None, [], None
    try:
        start_time = datetime.fromisoformat(window_start.replace("Z", "+00:00"))
        end_time = datetime.fromisoformat(window_end.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None, None, [], None
    if (
        start_time.tzinfo is None
        or end_time.tzinfo is None
        or end_time < start_time
    ):
        return None, None, [], None
    failed = []
    matching_count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if not _audit_identity_matches(entry, project=project, region=region, service=service):
            continue
        timestamp = entry.get("timestamp")
        if not isinstance(timestamp, str):
            continue
        try:
            observed_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        if observed_at.tzinfo is None or not start_time <= observed_at <= end_time:
            continue
        payload = entry.get("protoPayload")
        payload = payload if isinstance(payload, dict) else {}
        method = payload.get("methodName")
        if not isinstance(method, str) or not any(
            marker in method for marker in ("UpdateService", "CreateService", "ReplaceService")
        ):
            continue
        matching_count += 1
        status = payload.get("status")
        status = status if isinstance(status, dict) else entry.get("status")
        if not isinstance(status, dict):
            continue
        code = status.get("code")
        message = status.get("message")
        is_error = (isinstance(code, int) and code != 0) or (isinstance(message, str) and bool(message))
        if is_error:
            failed.append((code, message if isinstance(message, str) else ""))
    texts = [message for _, message in failed if message]
    if any(code == 7 for code, _ in failed):
        texts.append("permission_denied")
    status_codes = sorted({
        code for code, _ in failed
        if isinstance(code, int) and not isinstance(code, bool) and code in _KNOWN_RPC_STATUS_CODES
    })
    return matching_count, len(failed), texts, status_codes


def _control_summary(policy: Any, jobs: Any) -> dict[str, Any]:
    if (
        isinstance(policy, dict)
        and isinstance(policy.get("bindings"), list)
        and all(isinstance(binding, dict) for binding in policy["bindings"])
    ):
        binding_count: int | None = len(policy["bindings"])
    else:
        binding_count = None
    if isinstance(jobs, list) and all(isinstance(item, dict) for item in jobs):
        states = {"enabled": 0, "paused": 0, "other": 0}
        for job in jobs:
            state = job.get("state")
            if state == "ENABLED":
                states["enabled"] += 1
            elif state == "PAUSED":
                states["paused"] += 1
            else:
                states["other"] += 1
        job_count: int | None = len(jobs)
    else:
        states = {"enabled": None, "paused": None, "other": None}
        job_count = None
    return {
        "iam_policy_readable": binding_count is not None,
        "iam_binding_count": binding_count,
        "scheduler_readable": job_count is not None,
        "scheduler_job_count": job_count,
        "scheduler_state_counts": states,
    }


def _audit_message_terms(texts: list[str], *, available: bool) -> dict[str, bool | None]:
    if not available:
        return {term: None for term in _AUDIT_MESSAGE_TERMS}
    joined = "\n".join(texts)
    return {term: bool(_AUDIT_MESSAGE_TERM_PATTERNS[term].search(joined)) for term in _AUDIT_MESSAGE_TERMS}


def _tag_length_details(texts: list[str]) -> dict[str, Any]:
    messages = [text for text in texts if _REASON_PATTERNS["traffic_tag_length"].search(text)]
    text = "\n".join(messages)
    limits = sorted({int(value) for value in re.findall(r"\b([0-9]{1,2})\s+characters?\b", text, re.I) if 0 < int(value) <= 63})
    return {
        "character_limits": limits,
        "minimum_requirement": bool(re.search(r"at least|minimum|too short|shorter", text, re.I)),
        "maximum_requirement": bool(re.search(r"at most|maximum|too long|longer|exceed", text, re.I)),
    }


def summarize(
    *,
    service: Any,
    revisions: Any,
    expected_service: str,
    expected_project: str,
    expected_region: str,
    audit_window_start: str,
    audit_window_end: str,
    policy: Any,
    jobs: Any,
    audit_entries: Any,
    audit_status: str,
) -> dict[str, Any]:
    service_ok = isinstance(service, dict)
    metadata = service.get("metadata") if service_ok else None
    status = service.get("status") if service_ok else None
    status = status if isinstance(status, dict) else {}
    traffic = status.get("traffic")
    traffic_rows = traffic if isinstance(traffic, list) and all(isinstance(row, dict) for row in traffic) else None
    positive = []
    if traffic_rows is not None:
        for row in traffic_rows:
            percent = row.get("percent", 0)
            if isinstance(percent, int) and not isinstance(percent, bool) and percent > 0:
                positive.append((row, percent))
    traffic_valid = (
        traffic_rows is not None
        and len(positive) == 1
        and positive[0][1] == 100
        and isinstance(status.get("latestReadyRevisionName"), str)
        and positive[0][0].get("revisionName") == status.get("latestReadyRevisionName")
    )
    latest = _revision_for_service(service, revisions)
    revision_texts = _condition_error_texts(latest)
    latest_ready = _ready(latest)
    revision_categories = (
        _classify(revision_texts)
        if revision_texts
        else []
        if latest_ready is True
        else ["unknown"]
    )
    audit_entries_count, audit_error_count, audit_texts, audit_status_codes = _audit_error_texts(
        audit_entries,
        project=expected_project,
        region=expected_region,
        service=expected_service,
        window_start=audit_window_start,
        window_end=audit_window_end,
    )
    audit_categories = _classify(audit_texts) if audit_error_count else []
    audit_categories = audit_categories if audit_status == "ok" else ["unknown"]
    if audit_error_count and audit_status == "ok":
        failure_source = "audit"
        failure_categories = audit_categories
        failure_texts = audit_texts
    elif latest_ready is False:
        failure_source = "revision"
        failure_categories = revision_categories
        failure_texts = revision_texts
    else:
        failure_source = "none"
        failure_categories = ["unknown"]
        failure_texts = []
    combined_tag_name_error = _has_combined_tag_name_limit(failure_texts)
    target_matches = bool(isinstance(metadata, dict) and metadata.get("name") == expected_service)
    revision_status_codes = _condition_error_status_codes(latest)
    tag_budget_ok = bool(
        target_matches
        and isinstance(metadata, dict)
        and isinstance(metadata.get("name"), str)
        and len(metadata["name"]) + len(DIAGNOSTIC_TAG) <= MAX_SERVICE_AND_TRAFFIC_TAG_LENGTH
    )
    result = {
        "schema_version": "firstrade_cached_stage_diagnostic.v1",
        "service_readable": service_ok,
        "target_matches": target_matches,
        "diagnostic_tag_budget_ok": tag_budget_ok,
        "service_name_length": len(expected_service) if target_matches else None,
        "failure_subcategory": "combined_traffic_tag_service_name_length" if combined_tag_name_error else "none",
        "combined_traffic_tag_service_name_length_error_observed": combined_tag_name_error,
        "service_ready": _ready(service),
        "traffic_row_count": len(traffic_rows) if traffic_rows is not None else None,
        "positive_traffic_row_count": len(positive),
        "traffic_is_single_ready_revision_at_100_percent": bool(traffic_valid),
        "latest_created_revision_found": latest is not None,
        "revision_list_readable": isinstance(revisions, list)
        and all(isinstance(item, dict) for item in revisions),
        "latest_created_revision_ready": latest_ready,
        "latest_revision_error_categories": revision_categories,
        "latest_revision_error_status_codes": revision_status_codes,
        "audit_error_message_terms": _audit_message_terms(
            audit_texts,
            available=audit_status == "ok" and audit_entries_count is not None,
        ),
        "traffic_tag_length_details": _tag_length_details(audit_texts) if audit_status == "ok" else None,
        **_control_summary(policy, jobs),
        "audit_query_status": audit_status,
        "audit_entry_count": audit_entries_count if audit_status == "ok" else None,
        "audit_error_count": audit_error_count if audit_status == "ok" else None,
        "audit_error_categories": audit_categories,
        "audit_error_status_codes": audit_status_codes if audit_status == "ok" else None,
        "failure_source": failure_source,
        "failure_categories": failure_categories,
    }
    return result


def _validate_window(start: str, end: str) -> None:
    pattern = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z"
    if not re.fullmatch(pattern, start) or not re.fullmatch(pattern, end):
        raise ValueError("time_window_invalid")
    try:
        start_time = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_time = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("time_window_invalid") from exc
    seconds = (end_time - start_time).total_seconds()
    if seconds < 0 or seconds > 1800 or end_time > datetime.now(UTC):
        raise ValueError("time_window_invalid")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service")
    parser.add_argument("--revisions")
    parser.add_argument("--expected-service")
    parser.add_argument("--expected-project")
    parser.add_argument("--expected-region")
    parser.add_argument("--window-start")
    parser.add_argument("--window-end")
    parser.add_argument("--iam-policy")
    parser.add_argument("--scheduler-jobs")
    parser.add_argument("--audit-entries")
    parser.add_argument("--audit-status", choices=("ok", "permission_denied", "unavailable"))
    parser.add_argument("--validate-window", nargs=2, metavar=("START", "END"))
    args = parser.parse_args()
    if args.validate_window:
        try:
            _validate_window(*args.validate_window)
        except ValueError:
            print("time_window_invalid", file=sys.stderr)
            return 2
        return 0
    required = (
        args.service,
        args.revisions,
        args.expected_service,
        args.expected_project,
        args.expected_region,
        args.window_start,
        args.window_end,
        args.iam_policy,
        args.scheduler_jobs,
        args.audit_entries,
        args.audit_status,
    )
    if any(value is None for value in required):
        parser.error("summary inputs are required")
    summary = summarize(
        service=_read_json(args.service),
        revisions=_read_json(args.revisions),
        expected_service=args.expected_service,
        expected_project=args.expected_project,
        expected_region=args.expected_region,
        audit_window_start=args.window_start,
        audit_window_end=args.window_end,
        policy=_read_json(args.iam_policy),
        jobs=_read_json(args.scheduler_jobs),
        audit_entries=_read_json(args.audit_entries),
        audit_status=args.audit_status,
    )
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
