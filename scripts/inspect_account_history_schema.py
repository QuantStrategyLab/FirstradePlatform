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
from scripts.archive_account_history_page import (
    ArchiveUnavailable, PrivatePageArchive, native_page_from_archive, page_envelope,
)

_CONTAINERS = ("result", "data", "items", "rows", "transactions", "history", "account_history")
_FIELDS = ("date", "report_date", "trade_date", "settle_date", "transaction_date", "type", "transaction_type",
           "description", "symbol", "quantity", "price", "amount", "net_amount", "currency",
           "fee", "fees", "commission", "id", "transaction_id", "reference", "status", "side",
           "trade_price", "trans_str", "descriptionArray")
# Diagnostic candidates only: a matching name is not a provider field contract.
_FIELD_CANDIDATES = ("transactionId", "trans_id", "record_id", "activity_id", "ref_no",
    "order_id", "trade_id", "sequence", "currency_code", "ccy", "trans_type", "trans_code",
    "transaction_code", "cusip")


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


def _field_summary(rows, field):
    values = [row[field] for row in rows if field in row]
    type_names = {str: "str", int: "int", float: "float", bool: "bool",
                  list: "list", dict: "dict", type(None): "NoneType"}
    counts = {}
    for value in values:
        name = type_names.get(type(value), "unknown")
        counts[name] = counts.get(name, 0) + 1
    return values, {"present_count": len(values), "missing_count": len(rows) - len(values),
                    "type_counts": counts}


def _report_date(value):
    """Recognize only exact date-only forms; never guess a numeric locale."""
    if not isinstance(value, str):
        return None, "non_string", None
    for pattern, fmt, label in (
        (r"[0-9]{4}-[0-9]{2}-[0-9]{2}", "%Y-%m-%d", "iso_date"),
        (r"[0-9]{8}", "%Y%m%d", "compact_date"),
        (r"[0-9]{4}/[0-9]{2}/[0-9]{2}", "%Y/%m/%d", "year_first_slash"),
    ):
        if re.fullmatch(pattern, value):
            try:
                return datetime.strptime(value, fmt).date(), "parsed", label
            except ValueError:
                return None, "invalid", None
    if re.fullmatch(r"[0-9]{2}/[0-9]{2}/[0-9]{4}", value):
        candidates = set()
        for fmt in ("%m/%d/%Y", "%d/%m/%Y"):
            try:
                candidates.add(datetime.strptime(value, fmt).date())
            except ValueError:
                pass
        if len(candidates) == 1:
            return next(iter(candidates)), "parsed", "unambiguous_numeric_slash"
        return None, "ambiguous" if candidates else "invalid", None
    return None, "unknown_format", None


def _diagnostic_field(rows, field):
    values, result = _field_summary(rows, field)
    formats = dict.fromkeys(("empty", "date", "ambiguous_date", "uuid", "numeric", "opaque"), 0)
    for value in values:
        if type(value) is not str:
            continue
        parsed, status, _ = _report_date(value)
        if not value.strip():
            fmt = "empty"
        elif parsed is not None:
            fmt = "date"
        elif status == "ambiguous":
            fmt = "ambiguous_date"
        elif re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", value):
            fmt = "uuid"
        elif re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", value):
            fmt = "numeric"
        else:
            fmt = "opaque"
        formats[fmt] += 1
    scalars = [(type(value), value) for value in values
               if type(value) is int or (type(value) is str and value.strip())]
    result.update(string_format_counts=formats, nonempty_scalar_count=len(scalars),
        distinct_scalar_count=len(set(scalars)), duplicate_scalar_count=len(scalars) - len(set(scalars)))
    return result


def _field_name_category(field):
    name = re.sub(r"[^a-z]", "", field.lower())
    if "currency" in name or name == "ccy": return "currency_like"
    if "date" in name or "time" in name: return "date_like"
    if name.endswith(("id", "uuid", "ref", "number", "seq", "sequence", "key")): return "identity_like"
    if "type" in name or "code" in name or name == "action": return "type_like"
    return "other"


def _row_semantics(rows, window):
    dates, date_checks = _field_summary(rows, "report_date")
    date_checks.update(dict.fromkeys(("parsed_count", "invalid_count", "unknown_format_count",
        "ambiguous_count", "non_string_count", "in_window_count", "out_of_window_count"), 0))
    date_checks["parsed_format_counts"] = dict.fromkeys(("iso_date", "compact_date",
        "year_first_slash", "unambiguous_numeric_slash"), 0)
    for value in dates:
        parsed, status, fmt = _report_date(value)
        date_checks[status + "_count"] += 1
        if parsed is not None:
            date_checks["parsed_format_counts"][fmt] += 1
            date_checks["in_window_count" if window[0] <= parsed <= window[1] else "out_of_window_count"] += 1
    currencies, currency_checks = _field_summary(rows, "currency")
    usd_count = sum(type(value) is str and value == "USD" for value in currencies)
    currency_checks.update(known_currency_counts={"USD": usd_count},
                           unknown_value_count=len(currencies) - usd_count)
    native_ids = {}
    for field in ("id", "transaction_id", "reference"):
        values, checks = _field_summary(rows, field)
        scalars = [(type(value), value) for value in values
                   if type(value) is int or (type(value) is str and value.strip())]
        checks.update(nonempty_scalar_count=len(scalars), distinct_scalar_count=len(set(scalars)),
                      duplicate_scalar_count=len(scalars) - len(set(scalars)))
        native_ids[field] = checks
    keys = set().union(*(row.keys() for row in rows)) if rows else set()
    diagnostic_fields = (*_FIELD_CANDIDATES, "type", "transaction_type", "trans_str")
    unknown_fields = keys - set(_FIELDS) - set(_FIELD_CANDIDATES)
    return {"report_date": date_checks, "currency": currency_checks,
        "native_id_fields": native_ids, "native_identity_stability": "unknown",
        "native_field_diagnostics": {
            "fixed_fields": {field: _diagnostic_field(rows, field) for field in diagnostic_fields},
            "unrecognized_fields": [{"name_category": _field_name_category(field),
                **_diagnostic_field(rows, field)} for field in sorted(unknown_fields)[:16]],
            "unrecognized_field_count": len(unknown_fields),
            "unrecognized_fields_omitted_count": max(0, len(unknown_fields) - 16),
            "field_contract": "unknown"}}


def _pagination_semantics(containers, counts, labels):
    row_count = containers[0]["row_count"] if len(containers) == 1 else None
    result = {"row_count": row_count, "expected_row_count": None,
              "metadata_consistency": "unknown", "page_coverage": "unknown"}
    if (row_count is None or any(value is None for value in counts.values())
            or any(label in labels for label in ("total_pages", "next_page", "pagination"))):
        return result
    page, per_page, total = (counts[key] for key in ("page", "per_page", "total"))
    if page != 1 or per_page < 1:
        result["metadata_consistency"] = "inconsistent"
        return result
    expected = min(per_page, total)
    result["expected_row_count"] = expected
    result["metadata_consistency"] = "consistent" if row_count == expected else "inconsistent"
    if row_count == expected:
        result["page_coverage"] = "reported_single_page" if total <= per_page else "more_pages_reported"
    return result


def project_schema(payload, *, start=None, end=None):
    """Expose fixed field labels/types and counts; never values or dynamic names."""
    if not isinstance(payload, (dict, list)):
        raise ValueError("history_schema_invalid")
    window = None
    if start is not None or end is not None:
        try:
            first, last = date.fromisoformat(start), date.fromisoformat(end)
            if first.isoformat() != start or last.isoformat() != end or first > last:
                raise ValueError
            window = first, last
        except (ValueError, TypeError):
            raise ValueError("history_semantic_window_invalid") from None
    result, semantic_containers = [], []
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
            if window is not None:
                semantic_containers.append({"path": path, "row_count": len(value),
                                            **_row_semantics(value, window)})
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
    schema = {"containers": result, "pagination_labels_present": [key for key in
        ("page", "per_page", "total", "total_pages", "next_page", "pagination")
        if isinstance(payload, dict) and key in payload],
        "pagination_counts": pagination_counts, "provider_error_present": provider_error_present,
        "coverage": "first_page_only_not_complete_history", "financial_values_exported": False}
    if window is not None:
        schema["semantic_checks"] = {"containers": semantic_containers,
            "pagination": _pagination_semantics(result, pagination_counts, schema["pagination_labels_present"]),
            "transaction_qualification": "not_qualified"}
    return schema


def execute(start, end, *, archive_page=False, archive_run="", archive_generation="", archive_sha256=""):
    if any((archive_run, archive_generation, archive_sha256)):
        if (archive_page or not re.fullmatch(r"[1-9][0-9]{0,19}", archive_run)
                or not re.fullmatch(r"[1-9][0-9]{0,24}", archive_generation)
                or not re.fullmatch(r"[0-9a-f]{64}", archive_sha256)):
            raise ArchiveUnavailable("archive_selector_invalid")
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
    if not account or (not archive_run and runtime.get("FIRSTRADE_REUSE_SESSION", "false").lower() != "true"):
        raise ValueError("history_cached_runtime_unavailable")
    bucket = runtime.get("FIRSTRADE_GCS_STATE_BUCKET")
    prefix = (runtime.get("FIRSTRADE_STATE_PREFIX") or "firstrade-platform").strip("/")
    if not bucket or (not archive_run and runtime.get("FIRSTRADE_PERSIST_SESSION_CACHE", "false").lower() != "true"):
        raise ValueError("history_cache_store_unavailable")
    if archive_page or archive_run:
        if (not re.fullmatch(r"[A-Za-z0-9._/-]{1,512}", prefix)
                or any(part in ("", ".", "..") for part in prefix.split("/"))):
            raise ArchiveUnavailable("archive_scope_invalid")
        # Check private storage before consuming broker authentication or a page.
        archive = PrivatePageArchive(bucket)
    if archive_run:
        identity = hashlib.sha256(account.encode()).hexdigest()
        name = f"{prefix}/account-activity/{identity}/{start}_{end}/{archive_run}-1.json"
        body = archive.read_pinned(name, archive_generation, archive_sha256)
        payload = native_page_from_archive(body, account=account, start=start, end=end)
        schema = project_schema(payload, start=start, end=end)
        if schema["provider_error_present"] is True:
            raise ArchiveUnavailable("archive_provider_error_present")
        return {"status": "inspected", "from": start, "to": end,
            "account_selector_matches": True, "schema": schema,
            "archive_read_only": True, "archive_generation": archive_generation,
            "archive_sha256": archive_sha256, "broker_request_attempts": 0,
            "cache_reads": 0, "cache_writes": 0, "business_writes": 0, "archive_write_requests": 0}
    if archive_page:
        run_id, source = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_SHA", "")
        if (not re.fullmatch(r"[1-9][0-9]{0,19}", run_id)
                or not re.fullmatch(r"[0-9a-f]{40}", source)):
            raise ArchiveUnavailable("archive_scope_invalid")
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
    parser.add_argument("--archive-run", default="")
    parser.add_argument("--archive-generation", default="")
    parser.add_argument("--archive-sha256", default="")
    args = parser.parse_args()
    try:
        options = {"archive_page": True} if args.archive_page else {}
        if any((args.archive_run, args.archive_generation, args.archive_sha256)):
            options.update(archive_run=args.archive_run, archive_generation=args.archive_generation,
                           archive_sha256=args.archive_sha256)
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
