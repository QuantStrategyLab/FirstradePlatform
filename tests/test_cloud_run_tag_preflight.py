from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import verify_cached_diagnostic_stage as stage

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["Bad-Service", "service/other", " service", "service ", "service-", "1service", "s" * 64])
def test_diagnostic_rejects_invalid_exact_name_without_echo(name, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["verify_cached_diagnostic_stage.py", "tag-budget",
                                    "--expected-service", name, "--tag", "cbd"])
    assert stage.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "cached_diagnostic_stage_blocked:INVALID_SERVICE_NAME\n"
    assert name not in output.err


@pytest.mark.parametrize("name", ["Bad-Service", " service ", "service-", "s" * 44, "s" * 46, "s" * 48])
def test_plan_refuses_incompatible_exact_service_before_emitting_plan(name):
    env = {**os.environ, "PLATFORM_CONFIG_JSON": '{"strategies":{}}',
           "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps([{
               "service": name, "NOTIFY_LANG": "en", "runtime_target": {
                   "platform_id": "firstrade", "strategy_profile": "tqqq_growth_income",
                   "dry_run_only": True, "service_name": name}}])}
    result = subprocess.run([sys.executable, str(ROOT / "scripts/build_cloud_run_env_sync_plan.py"), "--json"],
                            env=env, text=True, capture_output=True)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.strip() in {"INVALID_SERVICE_NAME", "diagnostic_tag_name_budget_exceeded"}
    assert name not in result.stderr


def test_cloudbuild_preflight_precedes_build_and_deploy():
    text = (ROOT / "cloudbuild.yaml").read_text()
    assert text.index("id: ServicePreflight") < text.index("id: Buildpack") < text.index("id: Deploy")
    block = text.split("id: ServicePreflight", 1)[1].split("  - id:", 1)[0]
    assert "scripts/cloud_run_tag_preflight.py" in block
    assert "--service-name=$_SERVICE_NAME" in block
    assert "--diagnostic" not in block  # Image-only caller must not allocate a traffic tag.


@pytest.mark.parametrize(("length", "ok"), [(43, True), (44, False), (45, False), (46, False), (48, False)])
def test_fixed_diagnostic_never_shortens_or_enables_traffic(length, ok):
    name = "s" * length
    if ok:
        stage._validate_diagnostic_tag_budget(name, "cbd")
    else:
        with pytest.raises(ValueError, match="diagnostic_tag_name_budget_exceeded"):
            stage._validate_diagnostic_tag_budget(name, "cbd")


@pytest.mark.parametrize("mode", ["legacy", "runtime_identity", "runtime_fallback"])
def test_plan_never_strips_an_exact_service_identity(mode):
    name = " service-placeholder "
    target = {"platform_id": "firstrade", "strategy_profile": "tqqq_growth_income",
              "dry_run_only": True, "service_name": name}
    env = {**os.environ, "PLATFORM_CONFIG_JSON": '{"strategies":{}}',
           "NOTIFY_LANG": "en", "CLOUD_RUN_SERVICE_TARGETS_JSON": "",
           "CLOUD_RUN_SERVICES": ""}
    if mode == "legacy":
        env.update(CLOUD_RUN_SERVICE=name, RUNTIME_TARGET_JSON=json.dumps(target))
    else:
        row = {"NOTIFY_LANG": "en", "runtime_target": target}
        if mode == "runtime_identity":
            row["service"] = name.strip()
        env["CLOUD_RUN_SERVICE_TARGETS_JSON"] = json.dumps([row])
    result = subprocess.run([sys.executable, str(ROOT / "scripts/build_cloud_run_env_sync_plan.py"), "--json"],
                            env=env, text=True, capture_output=True)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "INVALID_SERVICE_NAME\n"


@pytest.mark.parametrize(("name", "ok"), [("s" * 48, True), ("s" * 63, True),
                                         ("s" * 64, False), ("bad/identity", False)])
def test_execute_cloudbuild_preflight_arguments_without_cloud_calls(name, ok):
    text = (ROOT / "cloudbuild.yaml").read_text()
    block = text.split("id: ServicePreflight", 1)[1].split("  - id:", 1)[0]
    args = [line.strip()[2:] for line in block.split("    args:", 1)[1].splitlines()
            if line.strip().startswith("- ")]
    args = [arg.replace("$_SERVICE_NAME", name) for arg in args]
    result = subprocess.run([sys.executable, *args], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == (0 if ok else 1)
    assert result.stdout == ("cloud_run_service_preflight_ok\n" if ok else "")
    assert result.stderr == ("" if ok else "INVALID_SERVICE_NAME\n")


def test_plan_retains_exact_service_at_diagnostic_budget_boundary():
    from scripts.build_cloud_run_env_sync_plan import build_sync_plan

    name = "s" * 43
    plan = build_sync_plan({"PLATFORM_CONFIG_JSON": '{"strategies":{}}',
                           "CLOUD_RUN_SERVICE": name, "NOTIFY_LANG": "en",
                           "RUNTIME_TARGET_JSON": json.dumps({"service_name": name,
                               "platform_id": "firstrade", "strategy_profile": "tqqq_growth_income",
                               "dry_run_only": True})})
    assert plan["targets"][0]["service_name"] == name
    assert json.loads(plan["targets"][0]["env"]["RUNTIME_TARGET_JSON"])["service_name"] == name
