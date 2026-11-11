"""Deploy only the already-reviewed native currency field in the cached archive."""
from __future__ import annotations

import ast
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

API = "https://run.googleapis.com/v2/"
MODULE = "application/session_check_service.py"
ANCHOR = '"total_equity": balances.get("provider_equity"),'
CURRENCY = '"currency": balances.get("currency"),'
STAGE = "configuration"
REVISION_METADATA = {
    "name", "uid", "generation", "createTime", "updateTime", "deleteTime", "expireTime",
    "launchStage", "service", "reconciling", "conditions", "observedGeneration", "logUri",
    "satisfiesPzs", "scalingStatus", "containerStatuses", "creator", "etag",
}
TEMPLATE_METADATA = {"revision", "client", "clientVersion"}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def patch_archive(source, reviewed):
    def archive(text):
        matches = [item for item in ast.parse(text).body if isinstance(item, ast.FunctionDef)
                   and item.name == "archive_cached_account_facts"]
        require(len(matches) == 1, "cached_archive_function_missing")
        return ast.get_source_segment(text, matches[0])

    original = archive(source)
    require(CURRENCY in archive(reviewed) and CURRENCY not in original and original.count(ANCHOR) == 1,
            "cached_archive_patch_not_applicable")
    updated = original.replace(ANCHOR, ANCHOR + "\n            " + CURRENCY, 1)
    candidate = source.replace(original, updated, 1)
    require(archive(candidate) == updated, "cached_archive_patch_invalid")
    return candidate


def verify_configuration(template, revision):
    wanted = {k: v for k, v in template.items() if k not in TEMPLATE_METADATA}
    actual = {k: v for k, v in revision.items() if k not in REVISION_METADATA | TEMPLATE_METADATA}
    expected, = wanted["containers"]
    found, = actual["containers"]
    if "name" not in expected:
        actual["containers"] = [{k: v for k, v in found.items() if k != "name"}]
    require(wanted == actual, "revision_configuration_mismatch")


def wait(request, operation):
    deadline = time.monotonic() + 240
    while not operation.get("done") and time.monotonic() < deadline:
        time.sleep(4)
        operation = request(operation["name"])
    require(operation.get("done") and not operation.get("error"), "cloud_run_operation_failed")


def revision_path(path, revision):
    if re.fullmatch(r"[a-z0-9-]+", revision):
        return path + "/revisions/" + revision
    require(revision.startswith(path + "/revisions/"), "revision_not_in_service")
    return revision


def rollout(request, path, build):
    global STAGE
    baseline = request(path)
    require(baseline["ingress"] == "INGRESS_TRAFFIC_INTERNAL_ONLY" and not baseline.get("reconciling"),
            "serving_state_not_stable")
    route, = [x for x in baseline["traffic"] if x.get("percent", 0) > 0]
    status, = [x for x in baseline["trafficStatuses"] if x.get("percent", 0) > 0]
    require(route["type"] == "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION"
            and route["percent"] == status["percent"] == 100 and not route.get("tag"),
            "existing_traffic_not_pinned")
    old_path = revision_path(path, status["revision"])
    require(old_path == revision_path(path, route["revision"]), "traffic_configuration_drift")
    active = request(old_path)
    template = copy.deepcopy(baseline["template"])
    verify_configuration(template, active)
    container, = template["containers"]
    env = {x["name"]: x.get("value") for x in container["env"]}
    require(env.get("FIRSTRADE_PERSIST_ACCOUNT_SNAPSHOT") == "true", "cached_archive_disabled")
    before_image = container["image"]
    STAGE = "build_verified_archive_patch"
    container["image"] = build(before_image)
    require(before_image != container["image"], "candidate_image_unchanged")
    template.pop("revision", None)
    template["labels"]["qsl-observation-patch"] = "cached-currency"
    body = {"name": path, "etag": baseline["etag"], "template": template}
    require(request(path)["etag"] == baseline["etag"], "baseline_changed")
    STAGE = "stage_zero_traffic"
    request(path + "?updateMask=template&validateOnly=true", "PATCH", body)
    require(request(path)["etag"] == baseline["etag"], "baseline_changed")
    wait(request, request(path + "?updateMask=template", "PATCH", body))
    staged = request(path)
    require(staged["traffic"] == baseline["traffic"] and staged["trafficStatuses"] == baseline["trafficStatuses"]
            and staged["ingress"] == baseline["ingress"] and not staged.get("reconciling"), "stage_changed_serving_state")
    new_path = revision_path(path, staged["latestCreatedRevision"])
    require(new_path != old_path, "staged_revision_missing")
    candidate = request(new_path)
    require(not candidate.get("reconciling") and any(
        x.get("type") == "Ready" and x.get("state") == "CONDITION_SUCCEEDED" for x in candidate.get("conditions", [])
    ), "candidate_not_ready")
    verify_configuration(template, candidate)
    verify_configuration(staged["template"], candidate)
    STAGE = "promote_currency_only"
    traffic = [{**row, "revision": new_path.rsplit("/", 1)[-1]} if row == route else row for row in baseline["traffic"]]
    payload = {"name": path, "etag": staged["etag"], "traffic": traffic}
    request(path + "?updateMask=traffic&validateOnly=true", "PATCH", payload)
    require(request(path)["etag"] == staged["etag"], "staged_service_changed")
    wait(request, request(path + "?updateMask=traffic", "PATCH", payload))
    final = request(path)
    routed, = [x for x in final["trafficStatuses"] if x.get("percent", 0) > 0]
    require(revision_path(path, routed["revision"]) == new_path and routed["percent"] == 100
            and final["template"] == staged["template"] and final["ingress"] == baseline["ingress"]
            and [x for x in final["traffic"] if x.get("tag")] == [x for x in baseline["traffic"] if x.get("tag")],
            "final_serving_readback_mismatch")


def command(*args, timeout=45):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=timeout).strip()


def build_image(base):
    require(re.fullmatch(r"[a-z0-9-]+-docker.pkg.dev/[a-z0-9_./-]+@sha256:[a-f0-9]{64}", base),
            "serving_image_not_immutable")
    ref = base.split("@")[0]
    host = ref.split("/")[0]
    reviewed_sha = os.environ["REVIEWED_WORKFLOW_SHA"]
    require(re.fullmatch(r"[a-f0-9]{40}", reviewed_sha), "reviewed_source_invalid")
    tag = ref + ":cached-native-currency-" + reviewed_sha[:12]
    command("gcloud", "auth", "configure-docker", host, "--quiet")
    command("docker", "pull", base, timeout=180)
    original = command("docker", "run", "--rm", "--network=none", "--entrypoint=cat", base, "/app/" + MODULE) + "\n"
    patched = patch_archive(original, Path(MODULE).read_text())
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "archive.py").write_text(patched)
        (root / "Dockerfile").write_text(f"FROM {base}\nCOPY archive.py /app/{MODULE}\n")
        command("docker", "build", "--network=none", "-t", tag, directory, timeout=180)
    old, new = json.loads(command("docker", "image", "inspect", base, tag))
    old_config = {k: v for k, v in old["Config"].items() if k != "Image"}
    new_config = {k: v for k, v in new["Config"].items() if k != "Image"}
    require(old_config == new_config and old["Architecture"] == new["Architecture"] and old["Os"] == new["Os"]
            and new["RootFS"]["Layers"][:-1] == old["RootFS"]["Layers"], "image_changed_outside_archive_layer")
    installed = command("docker", "run", "--rm", "--network=none", "--entrypoint=cat", tag, "/app/" + MODULE)
    require(installed == patched.rstrip(), "built_archive_source_mismatch")
    command("docker", "push", tag, timeout=180)
    digest = command("gcloud", "artifacts", "docker", "images", "describe", tag, "--format=value(image_summary.digest)")
    require(re.fullmatch(r"sha256:[a-f0-9]{64}", digest), "published_digest_invalid")
    return ref + "@" + digest


def main():
    project, region, service = (os.environ[k] for k in ("GCP_PROJECT_ID", "CLOUD_RUN_REGION", "CLOUD_RUN_SERVICE"))
    require(all(re.fullmatch(r"[a-z0-9-]+", x) for x in (project, region, service)), "service_binding_invalid")
    token = command("gcloud", "auth", "print-access-token")

    def request(path, method="GET", body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        with urlopen(Request(API + path, data=data, headers=headers, method=method), timeout=30) as response:
            result = json.load(response)
        require(not result.get("error"), "cloud_run_api_error")
        return result

    rollout(request, f"projects/{project}/locations/{region}/services/{service}", build_image)
    print('{"cached_currency_release_verified":true,"runtime_configuration_preserved":true}')


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        result = {"cached_currency_release_verified": False, "stage": STAGE, "error_type": type(error).__name__}
        if isinstance(error, ValueError) and re.fullmatch(r"[a-z_]+", str(error)):
            result["reason"] = str(error)
        if isinstance(error, HTTPError):
            result["http_status"] = error.code
        print(json.dumps(result))
        raise SystemExit(1) from None
