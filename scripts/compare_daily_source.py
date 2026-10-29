"""Compare one configured daily source without publishing or exposing its tuple."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess

from scripts.inspect_funds_snapshot_schema import configured_account


def _read_cloud(args):
    result = subprocess.run(["gcloud", *args, "--format=json", "--quiet"], capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise ValueError("metadata_unavailable")
    return json.loads(result.stdout)


def compare_source(env, *, reader=_read_cloud):
    nonce = env.get("RUNTIME_DAILY_COMPARE_NONCE") or ""
    if not re.fullmatch(r"[a-f0-9]{64}", nonce):
        raise ValueError("nonce_invalid")
    service, region, project = (env.get(key) for key in ("CLOUD_RUN_SERVICE", "CLOUD_RUN_REGION", "GCP_PROJECT_ID"))
    if not all(isinstance(value, str) and value for value in (service, region, project)):
        raise ValueError("config_invalid")
    current = reader(["run", "services", "describe", service, "--region", region, "--project", project])
    active = [row for row in (current.get("status") or {}).get("traffic", []) if row.get("percent", 0) > 0]
    if len(active) != 1 or active[0].get("percent") != 100:
        raise ValueError("serving_ambiguous")
    revision = reader(["run", "revisions", "describe", active[0]["revisionName"], "--region", region, "--project", project])
    containers = (revision.get("spec") or {}).get("containers") or []
    if len(containers) != 1:
        raise ValueError("container_ambiguous")
    rows = containers[0].get("env") or []
    if len({row.get("name") for row in rows}) != len(rows):
        raise ValueError("env_ambiguous")
    runtime = {row["name"]: row.get("value") for row in rows}
    target = json.loads(runtime.get("QSL_RUNTIME_TARGET_JSON") or runtime.get("RUNTIME_TARGET_JSON") or "null")
    workflow_target = json.loads(env.get("RUNTIME_TARGET_JSON") or "null")
    if not isinstance(target, dict) or not isinstance(workflow_target, dict):
        raise ValueError("target_invalid")
    for key in ("service_name", "strategy_profile", "account_scope", "account_selector", "deployment_selector"):
        if target.get(key) != workflow_target.get(key):
            raise ValueError("target_mismatch")
    account = configured_account(runtime)
    selector = target.get("account_selector")
    if not isinstance(selector, list) or len(selector) != 1 or account != selector[0] or target.get("service_name") != service:
        raise ValueError("selector_mismatch")
    source = env.get("FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID") or ""
    target_id = env.get("FIRSTRADE_ACCOUNT_FACTS_TARGET_ID") or ""
    if (runtime.get("FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID") != source
            or runtime.get("FIRSTRADE_ACCOUNT_FACTS_TARGET_ID") != target_id
            or runtime.get("FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE") != target.get("account_scope")
            or env.get("FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE") != target.get("account_scope")):
        raise ValueError("facts_binding_mismatch")
    material = {"platform": "firstrade", "service_name": service,
                "deployment_selector": target.get("deployment_selector"), "account_scope": target.get("account_scope"),
                "broker_account_id": account}
    actual = hashlib.sha256(json.dumps(material, separators=(",", ":")).encode()).hexdigest()
    if not re.fullmatch(r"[a-f0-9]{64}", source) or actual != source:
        raise ValueError("facts_binding_mismatch")
    binding = {"platform": "firstrade", "service_name": service, "strategy_profile": target.get("strategy_profile"),
               "account_scope": target.get("account_scope"), "account_selector": account,
               "deployment_selector": target.get("deployment_selector"), "source_binding_id": source, "target_id": target_id}
    if not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value, re.ASCII) for value in binding.values()):
        raise ValueError("tuple_invalid")
    value = {"binding": binding, "domain": "qsl.firstrade-daily-binding.v1", "nonce": nonce}
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def main():
    try:
        result = {"status": "verified", "binding_digest": compare_source(os.environ)}
    except Exception:
        result = {"status": "unavailable"}
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
