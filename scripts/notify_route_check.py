#!/usr/bin/env python3
"""Read-only Telegram route check for this platform's Cloud Run services.

For every Cloud Run service in the project (all regions), report:
- serving traffic split and the template (latest created) revision;
- for the most recent revisions: Ready state, TELEGRAM_TOKEN binding (secret
  name/version only), image, commit-sha label and runtime service account;
- whether the GitHub Actions deploy SA can read each referenced Telegram
  secret (plus the QuantSentinel contract secret), resolved to the public bot
  identity via Telegram getMe (bot id / username only);
- the accessor members on the contract secret, when readable.

Never prints token values or chat ids. Never changes Cloud Run, secrets,
IAM, schedulers or trading state.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request

CONTRACT_SECRET = "quant-sentinel-telegram-bot-token"
ACCESSOR_ROLE = "roles/secretmanager.secretAccessor"


def _gcloud(*args: str) -> str:
    return subprocess.run(["gcloud", *args], check=True, capture_output=True, text=True).stdout


def _reason(exc: subprocess.CalledProcessError) -> str:
    stderr = (exc.stderr or "").upper()
    for marker in ("NOT_FOUND", "PERMISSION_DENIED", "FAILED_PRECONDITION", "UNAUTHENTICATED"):
        if marker in stderr:
            return marker.lower()
    return "unknown"


def _ready(obj: dict) -> str | None:
    for cond in (obj.get("status") or {}).get("conditions") or []:
        if cond.get("type") == "Ready":
            return cond.get("status")
    return None


def _telegram_binding(spec: dict) -> dict:
    result = {"telegram_token_source": "missing", "telegram_token_secret": None,
              "telegram_token_secret_version": None}
    for container in spec.get("containers") or []:
        for env in container.get("env") or []:
            if env.get("name") != "TELEGRAM_TOKEN":
                continue
            ref = (env.get("valueFrom") or {}).get("secretKeyRef")
            if ref:
                result.update(telegram_token_source="secret_ref",
                              telegram_token_secret=ref.get("name"),
                              telegram_token_secret_version=ref.get("key"))
            elif env.get("value"):
                result["telegram_token_source"] = "plain_env_value"
    return result


def _image(spec: dict) -> str | None:
    containers = spec.get("containers") or []
    return containers[0].get("image") if containers else None


def _revision_summary(rev: dict, traffic: dict[str, int]) -> dict:
    meta = rev.get("metadata") or {}
    spec = rev.get("spec") or {}
    name = meta.get("name")
    out = {
        "revision": name,
        "created": meta.get("creationTimestamp"),
        "ready": _ready(rev),
        "traffic_percent": traffic.get(name, 0),
        "commit_sha_label": (meta.get("labels") or {}).get("commit-sha"),
        "image": _image(spec),
        "runtime_service_account": spec.get("serviceAccountName"),
    }
    out.update(_telegram_binding(spec))
    return out


def _bot_identity(secret: str, project: str) -> dict:
    try:
        token = _gcloud("secrets", "versions", "access", "latest", f"--secret={secret}",
                        f"--project={project}").strip()
    except subprocess.CalledProcessError as exc:
        return {"secret": secret, "deploy_sa_can_access": False, "reason": _reason(exc)}
    if not token:
        return {"secret": secret, "deploy_sa_can_access": True, "status": "secret_empty"}
    print(f"::add-mask::{token}")
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - report class only, never the URL
        return {"secret": secret, "deploy_sa_can_access": True, "status": f"getme_failed:{type(exc).__name__}"}
    finally:
        token = ""
    result = body.get("result") or {}
    return {
        "secret": secret,
        "deploy_sa_can_access": True,
        "status": "ok" if body.get("ok") else "getme_not_ok",
        "bot_id": result.get("id"),
        "bot_username": result.get("username"),
    }


def _accessor_members(secret: str, project: str) -> dict:
    try:
        policy = json.loads(_gcloud("secrets", "get-iam-policy", secret, f"--project={project}",
                                    "--format=json"))
    except subprocess.CalledProcessError as exc:
        return {"secret": secret, "policy_readable": False, "reason": _reason(exc)}
    members: list[str] = []
    for binding in policy.get("bindings") or []:
        if binding.get("role") == ACCESSOR_ROLE and not binding.get("condition"):
            members.extend(binding.get("members") or [])
    return {"secret": secret, "policy_readable": True, "unconditional_accessors": sorted(members)}


def main() -> int:
    project = os.environ["GCP_PROJECT_ID"]
    prefix = (os.environ.get("SERVICE_NAME_PREFIX") or "").strip()
    limit = int(os.environ.get("REVISION_LIMIT") or "5")
    report: dict = {"project": project, "service_name_prefix": prefix or None, "services": []}
    secrets = {CONTRACT_SECRET}
    services = json.loads(_gcloud("run", "services", "list", f"--project={project}", "--format=json"))
    for svc in services:
        meta = svc.get("metadata") or {}
        name = meta.get("name") or ""
        if prefix and not name.startswith(prefix):
            continue
        region = (meta.get("labels") or {}).get("cloud.googleapis.com/location")
        status = svc.get("status") or {}
        traffic = {t.get("revisionName"): int(t.get("percent") or 0)
                   for t in status.get("traffic") or [] if t.get("revisionName")}
        entry = {
            "service": name,
            "region": region,
            "traffic": [{"revision": k, "percent": v} for k, v in traffic.items() if v],
            "spec_traffic_uses_latest": any(t.get("latestRevision") for t in (svc.get("spec") or {}).get("traffic") or []),
            "latest_created_revision": status.get("latestCreatedRevisionName"),
            "latest_ready_revision": status.get("latestReadyRevisionName"),
            "template_revision_name": ((svc.get("spec") or {}).get("template") or {}).get("metadata", {}).get("name"),
            "template": _telegram_binding(((svc.get("spec") or {}).get("template") or {}).get("spec") or {}),
            "revisions": [],
        }
        try:
            revs = json.loads(_gcloud("run", "revisions", "list", f"--service={name}", f"--project={project}",
                                      f"--region={region}", "--format=json", f"--limit={limit}",
                                      "--sort-by=~metadata.creationTimestamp"))
        except subprocess.CalledProcessError as exc:
            entry["revisions_error"] = _reason(exc)
            revs = []
        listed = {((r.get("metadata") or {}).get("name")) for r in revs}
        for rev_name in traffic:
            if rev_name not in listed:
                try:
                    revs.append(json.loads(_gcloud("run", "revisions", "describe", rev_name, f"--project={project}",
                                                   f"--region={region}", "--format=json")))
                except subprocess.CalledProcessError as exc:
                    entry.setdefault("serving_describe_errors", []).append({"revision": rev_name, "reason": _reason(exc)})
        for rev in revs:
            summary = _revision_summary(rev, traffic)
            if summary.get("telegram_token_secret"):
                secrets.add(summary["telegram_token_secret"])
            entry["revisions"].append(summary)
        report["services"].append(entry)
    report["bots"] = [_bot_identity(secret, project) for secret in sorted(secrets)]
    report["contract_secret_policy"] = _accessor_members(CONTRACT_SECRET, project)
    contract_bot = next((b for b in report["bots"] if b["secret"] == CONTRACT_SECRET), {})
    for svc in report["services"]:
        for rev in svc["revisions"]:
            bound = next((b for b in report["bots"] if b["secret"] == rev.get("telegram_token_secret")), {})
            rev["same_bot_as_contract"] = bool(bound.get("bot_id")) and bound.get("bot_id") == contract_bot.get("bot_id")
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("## Telegram route check (read-only)\n\n```json\n" + text + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
