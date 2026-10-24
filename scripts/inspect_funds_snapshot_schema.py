"""Inspect existing private funds schemas in the cloud runner, without values."""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import datetime

_FIELD = re.compile(r"[A-Za-z][A-Za-z0-9_.]{0,95}\Z", re.ASCII)
_SENSITIVE = re.compile(r"account|acct|token|secret|password|cookie|session|[0-9]{6}", re.I)


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


def inspect_existing_snapshots() -> dict:
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
    objects = [r for r in listing.get("items", []) if r["name"].endswith("/funds/latest.json")]
    if not objects or len(objects) > 10:
        raise ValueError("snapshot_count_unavailable")
    rows = []
    for obj in objects:
        query = urllib.parse.urlencode({"alt": "media", "generation": obj["generation"]})
        snapshot = read_json(base + "/" + urllib.parse.quote(obj["name"], safe="") + "?" + query)
        rows.append(project_schema(snapshot))
    return {"status": "inspected", "snapshot_count": len(rows), "snapshots": rows, "broker_requests": 0, "mutations": 0}


def main() -> int:
    try:
        result = inspect_existing_snapshots()
    except urllib.error.HTTPError as exc:
        print(json.dumps({"status": "inspection_unavailable", "http_status": exc.code}))
        return 1
    except Exception:
        print(json.dumps({"status": "inspection_unavailable"}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
