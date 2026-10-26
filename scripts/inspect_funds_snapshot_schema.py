"""Inspect existing private funds schemas in the cloud runner, without values."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import date, datetime

_FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_.]{0,95}\Z", re.ASCII)
_SENSITIVE = re.compile(r"account|acct|token|secret|password|cookie|session|[0-9]{6}", re.I)
_HISTORY = re.compile(
    r"([^/]+)/funds/history/(\d{4})/(\d{2})/(\d{2})/(\d{8})T\d{6}Z\.json\Z", re.ASCII,
)
_BALANCE_LABELS = (
    "provider_equity", "equity", "total_value", "market_value", "cash_balance",
    "cash_balance_change", "available_cash", "buying_power", "margin_balance",
    "total_equity", "account_list_total_value",
)
_KNOWN_METRICS = (*_BALANCE_LABELS, *("result." + label for label in _BALANCE_LABELS))
_REPORT_CONTAINERS = ("summary", "diagnostics", "result", "data")
_REPORT_ROWS = ("submitted_orders", "orders", "orders_previewed", "orders_skipped",
                "fills", "trades", "executions", "transactions", "cashflows")
_REPORT_ROW_FIELDS = ("id", "order_id", "execution_id", "transaction_id", "reference",
                      "symbol", "quantity", "filled_quantity", "filled_qty", "price",
                      "amount", "net_amount", "currency", "fee", "commission", "status",
                      "side", "type", "date", "trade_date", "settle_date", "timestamp",
                      "filled_at", "execution_time")


def report_location(raw: str) -> tuple[str, str]:
    """Follow the shared report writer's platform subdirectory, never bucket-wide."""
    if any(ord(char) < 32 for char in raw):
        raise ValueError("report_location_invalid")
    uri = urllib.parse.urlsplit(raw)
    prefix = uri.path.lstrip("/").rstrip("/")
    if (uri.scheme != "gs" or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,221}", uri.netloc)
            or uri.query or uri.fragment or len(prefix) > 800
            or (prefix and any(part in {"", ".", ".."} for part in prefix.split("/")))
            or any(ord(char) < 32 for char in prefix)):
        raise ValueError("report_location_invalid")
    return uri.netloc, (prefix + "/" if prefix else "") + "firstrade/"


def project_report_schema(report: object, selected_account: str | None) -> dict:
    """Inspect known evidence shapes; submissions never become confirmed fills."""
    if not isinstance(report, dict):
        raise ValueError("report_shape_invalid")
    identities = {report[key] for key in ("account", "account_id")
                  if isinstance(report.get(key), str)
                  and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", report[key], re.ASCII)}
    native = next(iter(identities)) if len(identities) == 1 else None
    target = report.get("runtime_target")
    selector = target.get("account_selector") if isinstance(target, dict) else None
    if isinstance(selector, list):
        selector = selector[0] if len(selector) == 1 else None
    if not isinstance(selector, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", selector, re.ASCII):
        selector = None
    rows = []

    def visit(node, path, depth):
        if depth > 3:
            raise ValueError("report_schema_depth_exceeded")
        for key in _REPORT_ROWS:
            if key not in node:
                continue
            value = node[key]
            if not isinstance(value, list):
                rows.append({"path": path + "." + key, "container_type": type(value).__name__,
                    "row_count": None, "non_object_row_count": None,
                    "known_field_types": {}, "unlisted_field_count": None})
                continue
            if len(value) > 1000:
                raise ValueError("report_rows_invalid")
            objects = [row for row in value if isinstance(row, dict)]
            keys = set().union(*(row.keys() for row in objects)) if objects else set()
            rows.append({"path": path + "." + key, "container_type": "list", "row_count": len(value),
                "non_object_row_count": len(value) - len(objects),
                "known_field_types": {field: sorted({type(row[field]).__name__ for row in objects if field in row})
                                      for field in _REPORT_ROW_FIELDS if field in keys},
                "unlisted_field_count": len(keys - set(_REPORT_ROW_FIELDS))})
        for key in _REPORT_CONTAINERS:
            if isinstance(node.get(key), dict):
                visit(node[key], path + "." + key, depth + 1)

    visit(report, "$", 0)
    observed_date = None
    for key in ("started_at", "created_at", "as_of", "timestamp"):
        try:
            parsed = datetime.fromisoformat(report.get(key, "").replace("Z", "+00:00"))
            if parsed.tzinfo:
                observed_date = parsed.date().isoformat()
                break
        except (ValueError, TypeError, AttributeError):
            pass
    return {"observation_date": observed_date,
            "native_account_present": native is not None,
            "configured_account_matches": native == selected_account if native and selected_account else None,
            "runtime_selector_matches": selector == selected_account if selector and selected_account else None,
            "dry_run": report.get("dry_run") if type(report.get("dry_run")) is bool else None,
            "receipt_present": any(isinstance(report.get(key), dict) for key in
                                   ("execution_receipt", "runtime_execution_receipt")),
            "containers": rows, "qualified_execution_count": None,
            "coverage": "known_fields_only_not_execution_qualification",
            "financial_values_exported": False}


def configured_account(env: dict) -> str | None:
    """Follow the deployed target-selector precedence without resolving secrets."""
    def unique_object(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("runtime_target_ambiguous")
            obj[key] = value
        return obj

    direct = env.get("FIRSTRADE_ACCOUNT")
    selected = direct
    for key in ("QSL_RUNTIME_TARGET_JSON", "RUNTIME_TARGET_JSON"):
        if key not in env or env[key] == "":
            continue
        if env[key] is None:
            return None
        target = json.loads(env[key], object_pairs_hook=unique_object)
        if not isinstance(target, dict):
            raise ValueError("runtime_target_invalid")
        selected = target.get("account_selector")
        if isinstance(selected, list):
            selected = selected[0] if len(selected) == 1 else None
        if direct and direct != selected:
            raise ValueError("configured_account_mismatch")
        break
    if selected is None:
        return None
    if not isinstance(selected, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", selected, re.ASCII):
        raise ValueError("configured_account_invalid")
    return selected


def history_objects(listing: dict, prefix: str) -> list[tuple[dict, str, str]]:
    """Validate the complete bounded archive selection before reading bodies."""
    if listing.get("nextPageToken"):
        raise ValueError("snapshot_listing_incomplete")
    objects = []
    for obj in listing.get("items", []):
        name = obj.get("name", "")
        if not isinstance(name, str) or not name.startswith(prefix):
            raise ValueError("snapshot_path_invalid")
        relative = name[len(prefix):]
        if "/funds/history/" not in relative:
            continue
        match = _HISTORY.fullmatch(relative)
        if not match:
            raise ValueError("snapshot_path_invalid")
        account, year, month, day, stamp_date = match.groups()
        archive_date = date(int(year), int(month), int(day))
        if stamp_date != archive_date.strftime("%Y%m%d"):
            raise ValueError("snapshot_path_date_mismatch")
        if not re.fullmatch(r"[0-9]{1,25}", str(obj.get("generation", "")), re.ASCII):
            raise ValueError("snapshot_generation_invalid")
        objects.append((obj, account, archive_date.isoformat()))
    if not 1 <= len(objects) <= 64 or len({row[1] for row in objects}) > 10:
        raise ValueError("snapshot_count_unavailable")
    if len({row[0]["name"] for row in objects}) != len(objects):
        raise ValueError("snapshot_listing_duplicate")
    return sorted(objects, key=lambda row: row[0]["name"])


def project_history(snapshot: object, *, account: str, archive_date: str,
                    configured_account: str | None) -> dict:
    """Expose fixed schema labels, dates and identity comparisons, never values."""
    schema = project_schema(snapshot)
    metrics = snapshot["balance_metrics"]
    native_account = snapshot.get("account")
    identity_present = isinstance(native_account, str) and bool(native_account.strip())
    namespace = "".join(ch if ch.isalnum() else "_" for ch in native_account) if identity_present else None
    types = {}
    for key in _KNOWN_METRICS:
        if key in metrics:
            value = metrics[key]
            types[key] = ("number" if type(value) in (float, int) else
                          "string" if isinstance(value, str) else "other")
    observed = schema["observed_at"]
    return {
        "archive_date": archive_date,
        "observed_at": observed,
        "observation_date_matches_archive": observed[:10] == archive_date if observed else None,
        "identity_present": identity_present,
        "storage_account_matches": native_account == account if identity_present else None,
        "storage_namespace_matches": namespace == account if identity_present else None,
        "configured_account_available": bool(configured_account),
        "configured_account_matches": native_account == configured_account if configured_account and identity_present else None,
        "known_metric_types": types,
        "unlisted_metric_count": len(metrics) - len(types),
        "positions_present": schema["positions_present"],
        "account_summary_present": isinstance(snapshot.get("account_summaries"), list),
        "canonical_snapshot_sha256": hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(),
        ).hexdigest(),
    }


def project_schema(snapshot: object) -> dict:
    """Return schema labels and counts only, never native identity or values."""
    if not isinstance(snapshot, Mapping):
        raise ValueError("snapshot_shape_invalid")
    metrics = snapshot.get("balance_metrics")
    if not isinstance(metrics, Mapping):
        raise ValueError("balance_metrics_missing")
    fields = []
    for key in metrics:
        if isinstance(key, str) and _FIELD.fullmatch(key) and not _SENSITIVE.search(key):
            fields.append(key)
    observed = None
    try:
        parsed = datetime.fromisoformat(snapshot.get("as_of", "").replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            observed = parsed.isoformat()
    except (ValueError, TypeError, AttributeError):
        pass
    return {
        "observed_at": observed,
        "balance_field_names": sorted(fields),
        "balance_field_count": len(metrics),
        "redacted_field_count": len(metrics) - len(fields),
        "positions_present": "positions" in snapshot,
    }


def inspect_existing_snapshots(*, history: bool = False, reports: bool = False) -> dict:
    project = os.environ["GCP_PROJECT_ID"]
    region = os.environ["CLOUD_RUN_REGION"]
    service = os.environ["CLOUD_RUN_SERVICE"]

    def gcloud(*args):
        return subprocess.check_output(
            ["gcloud", *args, "--project", project, "--format=json", "--quiet"],
            text=True, stderr=subprocess.PIPE, timeout=45,
        )

    metadata = json.loads(gcloud("run", "services", "describe", service, "--region", region))
    serving = [r for r in metadata["status"]["traffic"] if r.get("percent") == 100]
    if len(serving) != 1:
        raise ValueError("serving_revision_ambiguous")
    revision = json.loads(gcloud("run", "revisions", "describe", serving[0]["revisionName"], "--region", region))
    env = {r["name"]: r.get("value") for r in revision["spec"]["containers"][0].get("env", [])}
    bucket = env.get("FIRSTRADE_GCS_STATE_BUCKET")
    prefix = (env.get("FIRSTRADE_STATE_PREFIX") or "firstrade-platform").strip("/") + "/accounts/"
    if reports:
        bucket, prefix = report_location(env.get("QSL_EXECUTION_REPORT_GCS_URI") or env.get("EXECUTION_REPORT_GCS_URI") or "")
    elif not bucket:
        raise ValueError("state_bucket_unavailable")
    token = subprocess.check_output(
        ["gcloud", "auth", "print-access-token"], text=True, stderr=subprocess.PIPE, timeout=30,
    ).strip()

    def read_json(url):
        request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise ValueError("snapshot_size_exceeded")
        return json.loads(body)

    base = "https://storage.googleapis.com/storage/v1/b/" + urllib.parse.quote(bucket, safe="") + "/o"
    listing = read_json(base + "?" + urllib.parse.urlencode({
        "prefix": prefix, "maxResults": 1000, "fields": "items(name,generation),nextPageToken",
    }))
    if listing.get("nextPageToken"):
        raise ValueError("snapshot_listing_incomplete")
    if reports:
        objects = listing.get("items", [])
        if len(objects) > 64 or len({r.get("name") for r in objects}) != len(objects):
            raise ValueError("report_listing_invalid")
        for obj in objects:
            if (not isinstance(obj.get("name"), str) or not obj["name"].startswith(prefix)
                    or not obj["name"].endswith(".json")
                    or not re.fullmatch(r"[0-9]{1,25}", str(obj.get("generation", "")), re.ASCII)):
                raise ValueError("report_listing_invalid")
        rows = []
        for obj in sorted(objects, key=lambda row: row["name"]):
            query = urllib.parse.urlencode({"alt": "media", "generation": obj["generation"]})
            report = read_json(base + "/" + urllib.parse.quote(obj["name"], safe="") + "?" + query)
            rows.append(project_report_schema(report, configured_account(env)))
        return {"status": "inspected", "mode": "execution_report_schema", "report_count": len(rows),
                "reports": rows, "listing_complete": True, "broker_requests": 0, "mutations": 0}
    if history:
        selected_account = configured_account(env)
        objects = history_objects(listing, prefix)
        groups = {account: index + 1 for index, account in enumerate(sorted({row[1] for row in objects}))}
        rows = []
        for obj, account, archive_date in objects:
            query = urllib.parse.urlencode({"alt": "media", "generation": obj["generation"]})
            snapshot = read_json(base + "/" + urllib.parse.quote(obj["name"], safe="") + "?" + query)
            row = project_history(snapshot, account=account, archive_date=archive_date,
                                  configured_account=selected_account)
            rows.append({"account_group": groups[account], **row})
        return {"status": "inspected", "mode": "history_schema", "snapshot_count": len(rows),
                "account_group_count": len(groups), "snapshots": rows, "broker_requests": 0, "mutations": 0}
    objects = [r for r in listing.get("items", []) if r["name"].endswith("/funds/latest.json")]
    if not objects or len(objects) > 10:
        raise ValueError("snapshot_count_unavailable")
    rows = []
    for obj in objects:
        query = urllib.parse.urlencode({"alt": "media", "generation": obj["generation"]})
        snapshot = read_json(base + "/" + urllib.parse.quote(obj["name"], safe="") + "?" + query)
        rows.append(project_schema(snapshot))
    return {"status": "inspected", "snapshot_count": len(rows), "snapshots": rows, "broker_requests": 0, "mutations": 0}


def main(*, history: bool = False, reports: bool = False) -> int:
    try:
        if history and reports:
            raise ValueError("inspection_mode_ambiguous")
        result = (inspect_existing_snapshots(reports=True) if reports else
                  inspect_existing_snapshots(history=True) if history else inspect_existing_snapshots())
    except urllib.error.HTTPError as exc:
        print(json.dumps({"status": "inspection_unavailable", "http_status": exc.code}))
        return 1
    except ValueError as exc:
        result = {"status": "inspection_unavailable"}
        if reports:
            allowed = {"report_location_invalid", "report_shape_invalid", "report_schema_depth_exceeded",
                       "report_rows_invalid", "report_listing_invalid", "snapshot_listing_incomplete"}
            result["reason"] = str(exc) if str(exc) in allowed else "report_schema_invalid"
        print(json.dumps(result))
        return 1
    except Exception:
        print(json.dumps({"status": "inspection_unavailable"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="Inspect at most 64 saved archives; no broker calls")
    parser.add_argument("--reports", action="store_true", help="Inspect at most 64 saved report schemas; no broker calls")
    args = parser.parse_args()
    raise SystemExit(main(history=args.history, reports=args.reports))
