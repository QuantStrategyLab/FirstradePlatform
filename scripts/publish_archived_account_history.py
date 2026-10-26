"""Preview or append one saved account day in the cloud; no broker requests."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
import subprocess
import urllib.parse
import urllib.request

from application.account_facts_publisher import (
    build_firstrade_account_snapshot,
    load_account_facts_publish_config,
    publish_firstrade_account_snapshot,
)
from application.account_facts_readonly import _exact_balance_value
from scripts.inspect_funds_snapshot_schema import configured_account, history_objects


def archive_payload(snapshot, *, archive_date, config):
    """Consume only the exact identity, date and balance labels saved by producer."""
    if not isinstance(snapshot, dict) or snapshot.get("account") != config.account_id:
        raise ValueError("archive_identity_mismatch")
    observed = datetime.fromisoformat(snapshot.get("as_of", "").replace("Z", "+00:00"))
    if observed.tzinfo is None or observed.astimezone(timezone.utc).date().isoformat() != archive_date:
        raise ValueError("archive_date_mismatch")
    metrics = snapshot.get("balance_metrics")
    if not isinstance(metrics, dict):
        raise ValueError("archive_balances_missing")
    # Saved metrics are flattened by selected_numeric_metrics. Never use
    # cash_balance_change, buying_power or margin_balance as cash or cash flow.
    equity = _exact_balance_value(metrics, ("total_equity", "total_value", "account_list_total_value"))
    cash = _exact_balance_value(metrics, ("cash_balance", "result.cash_balance"))
    if equity is None or cash is None:
        raise ValueError("archive_balance_unavailable")
    observation = {
        "status": "available", "platform": "firstrade", "account_selector_status": "matched",
        "broker_account_id": snapshot["account"],
        # Only one observation instant was archived; do not replace it with now.
        "observed_started_at": observed.isoformat(), "observed_finished_at": observed.isoformat(),
        "balances": {"provider_equity": equity, "cash_balance": cash, "currency": "USD"},
    }
    return build_firstrade_account_snapshot(
        observation, replace(config, position_currency=None), account_scope=config.account_scope,
    )


def selected_archive(listing, prefix, *, account, archive_date):
    namespace = "".join(char if char.isalnum() else "_" for char in account)
    candidates = [
        obj for obj, saved_account, day in history_objects(listing, prefix)
        if saved_account == namespace and day == archive_date
    ]
    if len(candidates) != 1:
        raise ValueError("archive_selection_not_unique")
    return candidates[0]


def execute(archive_date, *, publish=False, env=None):
    env = os.environ if env is None else env
    day = date.fromisoformat(archive_date)
    today = datetime.now(timezone.utc).date()
    if day.isoformat() != archive_date or not 0 < (today - day).days < 366:
        raise ValueError("archive_date_out_of_range")
    project, region, service = (env[key] for key in ("GCP_PROJECT_ID", "CLOUD_RUN_REGION", "CLOUD_RUN_SERVICE"))
    if not all((project, region, service)):
        raise ValueError("cloud_configuration_missing")

    def cloud(*args):
        result = subprocess.run(
            ["gcloud", *args, "--project", project, "--format=json", "--quiet"],
            capture_output=True, text=True, timeout=45,
        )
        if result.returncode:
            raise ValueError("cloud_metadata_unavailable")
        return json.loads(result.stdout)

    metadata = cloud("run", "services", "describe", service, "--region", region)
    traffic = [row for row in metadata["status"]["traffic"] if row.get("percent", 0) > 0]
    if len(traffic) != 1 or traffic[0]["percent"] != 100:
        raise ValueError("serving_revision_ambiguous")
    revision = cloud("run", "revisions", "describe", traffic[0]["revisionName"], "--region", region)
    entries = revision["spec"]["containers"][0].get("env", [])
    runtime = {item["name"]: item.get("value") for item in entries}
    account = configured_account(runtime)
    if not account:
        raise ValueError("runtime_account_unavailable")
    target = json.loads(runtime.get("QSL_RUNTIME_TARGET_JSON") or runtime.get("RUNTIME_TARGET_JSON") or "{}")
    settings = dict(env)
    # Preview needs no token. For publishing consume only this platform's
    # existing serving token, in cloud-runner memory, never through an artifact.
    settings["FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN"] = "archive-preview-no-credential"
    config = load_account_facts_publish_config(settings, expected_account_id=account)
    if target.get("service_name") != service or target.get("account_scope") != config.account_scope:
        raise ValueError("runtime_identity_mismatch")
    stable_identity = {
        "platform": "firstrade", "service_name": service,
        "deployment_selector": target.get("deployment_selector"),
        "account_scope": config.account_scope, "broker_account_id": account,
    }
    expected_source = hashlib.sha256(json.dumps(stable_identity, separators=(",", ":")).encode()).hexdigest()
    if not stable_identity["deployment_selector"] or expected_source != config.source_binding_id:
        raise ValueError("source_binding_mismatch")
    bucket = runtime.get("FIRSTRADE_GCS_STATE_BUCKET")
    if not bucket:
        raise ValueError("state_bucket_unavailable")
    prefix = (runtime.get("FIRSTRADE_STATE_PREFIX") or "firstrade-platform").strip("/") + "/accounts/"
    token_result = subprocess.run(
        ["gcloud", "auth", "print-access-token"], capture_output=True, text=True, timeout=30,
    )
    if token_result.returncode:
        raise ValueError("cloud_identity_unavailable")
    token = token_result.stdout.strip()

    def read_json(url):
        request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise ValueError("archive_size_exceeded")
        return json.loads(body, parse_float=Decimal)

    base = "https://storage.googleapis.com/storage/v1/b/" + urllib.parse.quote(bucket, safe="") + "/o"
    listing = read_json(base + "?" + urllib.parse.urlencode({
        "prefix": prefix, "maxResults": 1000, "fields": "items(name,generation),nextPageToken",
    }))
    selected = selected_archive(listing, prefix, account=account, archive_date=archive_date)
    query = urllib.parse.urlencode({"alt": "media", "generation": selected["generation"]})
    snapshot = read_json(base + "/" + urllib.parse.quote(selected["name"], safe="") + "?" + query)
    payload = archive_payload(snapshot, archive_date=archive_date, config=config)
    if publish:
        token = runtime.get("FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN")
        if not token:
            reference = next((item.get("valueFrom", {}).get("secretKeyRef") for item in entries
                              if item.get("name") == "FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN"), None)
            if not isinstance(reference, dict) or not reference.get("name") or not reference.get("key"):
                raise ValueError("dedicated_sync_token_unavailable")
            result = subprocess.run([
                "gcloud", "secrets", "versions", "access", reference["key"],
                "--secret", reference["name"], "--project", project, "--quiet",
            ], capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise ValueError("dedicated_sync_token_unavailable")
            token = result.stdout.strip()
        settings["FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN"] = token
        config = load_account_facts_publish_config(settings, expected_account_id=account)
        publish_firstrade_account_snapshot(payload, config, historical=True)
    return {
        "status": "published" if publish else "preview", "observation_date": archive_date,
        "observed_finished_at": payload["observed_finished_at"], "selected_days": 1,
        "identity_matches_current": True, "source_binding_verified": True,
        "broker_requests": 0, "publish_requests": int(publish),
        "history_only_ack_verified": publish, "latest_updated": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    try:
        result = execute(args.date, publish=args.publish)
    except Exception as error:
        # No provider exception text, paths, identities, credentials or bodies.
        safe_reasons = {
            "archive_identity_mismatch", "archive_date_mismatch", "archive_balances_missing",
            "archive_balance_unavailable", "archive_selection_not_unique", "archive_date_out_of_range",
            "cloud_configuration_missing", "cloud_metadata_unavailable", "serving_revision_ambiguous",
            "runtime_account_unavailable", "runtime_identity_mismatch", "source_binding_mismatch",
            "state_bucket_unavailable", "cloud_identity_unavailable", "archive_size_exceeded",
            "dedicated_sync_token_unavailable", "snapshot_listing_incomplete",
            "sync_unavailable", "sync_rejected", "sync_ack_invalid", "balances_ambiguous",
        }
        reason = getattr(error, "reason_code", None)
        if reason not in safe_reasons:
            reason = str(error) if isinstance(error, ValueError) and str(error) in safe_reasons else "unavailable"
        print(json.dumps({"status": "archive_publish_unavailable", "reason": reason}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
