import importlib.util
import json
from pathlib import Path

import pytest


path = Path(__file__).resolve().parents[1] / "scripts" / "verify_deployed_runtime_target_admission.py"
spec = importlib.util.spec_from_file_location("deployed_target_admission", path)
assert spec is not None and spec.loader is not None
admission = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admission)


def payload(target, profile, dry_run="true"):
    return {"spec": {"template": {"spec": {"containers": [{"env": [
        {"name": "RUNTIME_TARGET_JSON", "value": json.dumps(target)},
        {"name": "STRATEGY_PROFILE", "value": profile},
        {"name": "FIRSTRADE_DRY_RUN_ONLY", "value": dry_run},
        {"name": "RUNTIME_TARGET_ENABLED", "value": "true"},
    ]}]}}}}


def target(profile="ibit_smart_dca", dry_run=True):
    return {"platform_id": "firstrade", "service_name": "live-service", "strategy_profile": profile, "execution_mode": "paper" if dry_run else "live", "dry_run_only": dry_run}


def test_admitted_shadow_target_passes():
    assert admission.verify_service(service="live-service", service_json=payload(target(), "ibit_smart_dca"))["profile"] == "ibit_smart_dca"


def test_paper_broker_submission_target_passes():
    configured = target(dry_run=False) | {"execution_mode": "paper"}
    assert admission.verify_service(service="live-service", service_json=payload(configured, "ibit_smart_dca", "false"))["dry_run_only"] is False


@pytest.mark.parametrize(
    ("configured", "profile", "message"),
    [
        (target(), "different_profile", "STRATEGY_PROFILE does not match"),
        (target() | {"execution_mode": "live"}, "ibit_smart_dca", "dry-run/shadow target"),
        (target("retired_profile"), "retired_profile", "not admitted"),
    ],
)
def test_target_drift_fails_closed(configured, profile, message):
    with pytest.raises(admission.AdmissionError, match=message):
        admission.verify_service(service="live-service", service_json=payload(configured, profile))
