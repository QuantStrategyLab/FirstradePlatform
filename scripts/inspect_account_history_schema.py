"""Inspect one cached-session account-history page in the cloud, without values."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from application.firstrade_client import (
    FirstradeBrokerClient, FirstradeCachedSessionError, FirstradeCredentials,
)
from scripts.inspect_funds_snapshot_schema import configured_account
from scripts.archive_account_history_page import ArchiveUnavailable, PrivatePageArchive, page_envelope

_CONTAINERS = ("result", "data", "items", "rows", "transactions", "history", "account_history")
_FIELDS = ("date", "report_date", "trade_date", "settle_date", "transaction_date", "type", "transaction_type",
           "description", "symbol", "quantity", "price", "amount", "net_amount", "currency",
           "fee", "fees", "commission", "id", "transaction_id", "reference", "status", "side")


class CloudReadUnavailable(ValueError):
    def __init__(self, stage, category="unclassified"):
        super().__init__("history_cloud_read_unavailable")
        self.stage = stage
        self.category = category


def cloud_error_category(stderr):
    """Map private CLI diagnostics to closed codes without exporting text."""
    for code, markers in (
        ("permission_denied", ("PERMISSION_DENIED", "secretmanager.versions.access' denied")),
        ("not_found", ("NOT_FOUND",)),
        ("authentication_failed", ("UNAUTHENTICATED", "invalid_grant", "Reauthentication")),
        ("service_disabled", ("SERVICE_DISABLED",)),
        ("argument_invalid", ("unrecognized arguments", "Invalid choice")),
    ):
        if any(marker in stderr for marker in markers):
            return code
    return "unclassified"


def project_schema(payload):
    """Expose fixed field labels/types and counts; never values or dynamic names."""
    if not isinstance(payload, (dict, list)):
        raise ValueError("history_schema_invalid")
    result = []
    def visit(value, path, depth):
        if depth > 4:
            raise ValueError("history_schema_depth_exceeded")
        if isinstance(value, list):
            if len(value) > 1000 or any(not isinstance(row, dict) for row in value):
                raise ValueError("history_rows_invalid")
            keys = set().union(*(row.keys() for row in value)) if value else set()
            result.append({"path": path, "row_count": len(value),
                "known_field_types": {key: sorted({type(row[key]).__name__ for row in value if key in row})
                                      for key in _FIELDS if key in keys},
                "unlisted_field_count": len(keys - set(_FIELDS))})
        elif isinstance(value, dict):
            for key in _CONTAINERS:
                if key in value and isinstance(value[key], (dict, list)):
                    visit(value[key], path + "." + key, depth + 1)
    visit(payload, "$", 0)
    pagination_counts = {}
    for key, maximum in (("page", 1000000), ("per_page", 1000), ("total", 1000000)):
        value = payload.get(key) if isinstance(payload, dict) else None
        pagination_counts[key] = value if type(value) is int and 0 <= value <= maximum else None
    provider_error_present = None
    if isinstance(payload, dict) and "error" in payload:
        provider_error_present = payload["error"] not in (None, "", False)
    return {"containers": result, "pagination_labels_present": [key for key in
        ("page", "per_page", "total", "total_pages", "next_page", "pagination")
        if isinstance(payload, dict) and key in payload],
        "pagination_counts": pagination_counts, "provider_error_present": provider_error_present,
        "coverage": "first_page_only_not_complete_history", "financial_values_exported": False}


def execute(start, end, *, archive_page=False):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    today = datetime.now(timezone.utc).date()
    if (first.isoformat() != start or last.isoformat() != end
            or not 0 <= (last - first).days <= 30
            or not 0 < (today - last).days <= (today - first).days <= 365):
        raise ValueError("history_window_invalid")
    project, region, service = (os.environ[key] for key in
        ("GCP_PROJECT_ID", "CLOUD_RUN_REGION", "CLOUD_RUN_SERVICE"))
    def cloud(*args, limit=2 * 1024 * 1024):
        response = subprocess.run(["gcloud", *args, "--project", project, "--quiet"],
            capture_output=True, text=True, timeout=45)
        if response.returncode or len(response.stdout.encode()) > limit:
            raise CloudReadUnavailable({"run": "runtime_metadata", "secrets": "cached_username",
                "storage": "saved_session"}[args[0]], cloud_error_category(response.stderr)
                if response.returncode else "response_size_exceeded")
        return response.stdout
    service_metadata = json.loads(cloud("run", "services", "describe", service, "--region", region, "--format=json"))
    traffic = [row for row in service_metadata["status"]["traffic"] if row.get("percent", 0) > 0]
    if len(traffic) != 1 or traffic[0]["percent"] != 100:
        raise ValueError("history_serving_revision_ambiguous")
    revision = json.loads(cloud("run", "revisions", "describe", traffic[0]["revisionName"], "--region", region, "--format=json"))
    runtime = {row["name"]: row.get("value") for row in revision["spec"]["containers"][0].get("env", [])}
    account = configured_account(runtime)
    if not account or runtime.get("FIRSTRADE_REUSE_SESSION", "false").lower() != "true":
        raise ValueError("history_cached_runtime_unavailable")
    bucket = runtime.get("FIRSTRADE_GCS_STATE_BUCKET")
    prefix = (runtime.get("FIRSTRADE_STATE_PREFIX") or "firstrade-platform").strip("/")
    if not bucket or runtime.get("FIRSTRADE_PERSIST_SESSION_CACHE", "false").lower() != "true":
        raise ValueError("history_cache_store_unavailable")
    if archive_page:
        run_id, source = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_SHA", "")
        if (not re.fullmatch(r"[1-9][0-9]{0,19}", run_id)
                or not re.fullmatch(r"[0-9a-f]{40}", source)
                or not re.fullmatch(r"[A-Za-z0-9._/-]{1,512}", prefix)
                or any(part in ("", ".", "..") for part in prefix.split("/"))):
            raise ArchiveUnavailable("archive_scope_invalid")
        # Check private storage before consuming broker authentication or a page.
        archive = PrivatePageArchive(bucket)
    # Consume only the existing username and saved tokens, never password or MFA.
    username = cloud("secrets", "versions", "access", "latest", "--secret=firstrade-username", limit=1024).strip()
    if not username:
        raise ValueError("history_username_unavailable")
    key = "sessions/" + hashlib.sha256(username.lower().encode()).hexdigest()[:16] + "/latest.json"
    # Read failures stay failures; do not let the client's cache fallback label
    # a denied/missing object as an expired authentication session.
    saved_payload = json.loads(cloud("storage", "cat", f"gs://{bucket}/{prefix}/{key}", limit=64 * 1024))
    class ReadOnlyCache:
        def read_json(self, requested_key):
            if requested_key != key:
                raise ValueError("history_cache_identity_mismatch")
            return saved_payload
        def write_json(self, *_args):
            raise ValueError("history_cache_write_forbidden")
    credentials = FirstradeCredentials(username=username, password="", reuse_session=True,
        session_cache_ttl_seconds=int(runtime.get("FIRSTRADE_SESSION_CACHE_TTL_SECONDS") or "21600"),
        cookie_dir=str(Path(os.environ["RUNNER_TEMP"]) / "unused-history-cache"))
    client = FirstradeBrokerClient(credentials, session_cache_store=ReadOnlyCache())
    try:
        client.connect_read_only()
        payload = client.get_account_history_read_only(account, start, end)
        schema = project_schema(payload)
        if schema["provider_error_present"] is True:
            return {"status": "stopped", "reason": "history_provider_reported_error",
                "broker_request_attempts": client.read_only_request_attempts,
                "cache_writes": 0, "business_writes": 0}
        archived = {}
        if archive_page:
            body, summary = page_envelope(client.read_only_history_response_bytes,
                account=account, start=start, end=end, source=source,
                observed_at=datetime.now(timezone.utc).isoformat())
            identity = hashlib.sha256(account.encode()).hexdigest()
            name = f"{prefix}/account-activity/{identity}/{start}_{end}/{run_id}-1.json"
            archived = {**archive.create(name, body), **summary}
        return {"status": "inspected", "from": start, "to": end,
            "account_selector_matches": True, "schema": schema,
            "broker_request_attempts": client.read_only_request_attempts,
            "history_page_limit": 1, "cache_writes": 0, "business_writes": 0, **archived}
    except FirstradeCachedSessionError as error:
        return {"status": "stopped", "reason": error.reason_code, "stage": error.stage,
            "http_status": error.http_status, "broker_request_attempts": client.read_only_request_attempts,
            "cache_writes": 0, "business_writes": 0}
    finally:
        client.close()


def main():
    if (os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"
            or os.environ.get("RUNNER_OS") != "Linux"):
        print(json.dumps({"status": "stopped", "reason": "history_cloud_manual_main_once_required"}))
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--to-date", required=True)
    parser.add_argument("--archive-page", action="store_true")
    args = parser.parse_args()
    try:
        options = {"archive_page": True} if args.archive_page else {}
        result = execute(args.from_date, args.to_date, **options)
    except ArchiveUnavailable as error:
        result = {"status": "stopped", "reason": error.reason,
                  "automatic_retry": False, "business_writes": 0}
    except CloudReadUnavailable as error:
        result = {"status": "stopped", "reason": "history_cloud_read_failed",
                  "stage": error.stage, "failure_category": error.category}
    except Exception:
        result = {"status": "stopped", "reason": "history_schema_inspection_unavailable"}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "inspected" else 1


if __name__ == "__main__":
    raise SystemExit(main())
