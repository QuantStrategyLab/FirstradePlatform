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


def inspect_existing_snapshots(*, history: bool = False) -> dict:
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
    if not bucket:
        raise ValueError("state_bucket_unavailable")
    prefix = (env.get("FIRSTRADE_STATE_PREFIX") or "firstrade-platform").strip("/") + "/accounts/"
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


def main(*, history: bool = False) -> int:
    try:
        result = inspect_existing_snapshots(history=True) if history else inspect_existing_snapshots()
    except urllib.error.HTTPError as exc:
        print(json.dumps({"status": "inspection_unavailable", "http_status": exc.code}))
        return 1
    except Exception:
        print(json.dumps({"status": "inspection_unavailable"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="Inspect at most 64 saved archives; no broker calls")
    raise SystemExit(main(history=parser.parse_args().history))
