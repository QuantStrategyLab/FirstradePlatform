import hashlib
import json

import pytest

from scripts.compare_daily_source import compare_source


def fixture():
    target = {"service_name": "synthetic-service", "strategy_profile": "synthetic-profile", "account_scope": "live",
              "account_selector": ["synthetic-account"], "deployment_selector": "synthetic-deployment"}
    material = {"platform": "firstrade", "service_name": "synthetic-service", "deployment_selector": "synthetic-deployment",
                "account_scope": "live", "broker_account_id": "synthetic-account"}
    source = hashlib.sha256(json.dumps(material, separators=(",", ":")).encode()).hexdigest()
    values = {"RUNTIME_TARGET_JSON": json.dumps(target), "FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID": source,
              "FIRSTRADE_ACCOUNT_FACTS_TARGET_ID": "synthetic-target", "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE": "live"}
    env = dict(values, CLOUD_RUN_SERVICE="synthetic-service", CLOUD_RUN_REGION="us-central1",
               GCP_PROJECT_ID="synthetic-project", RUNTIME_DAILY_COMPARE_NONCE="a" * 64)
    service = {"status": {"traffic": [{"percent": 100, "revisionName": "synthetic-revision"}]}}
    rev = {"spec": {"containers": [{"env": [{"name": k, "value": v} for k, v in values.items()]}]}}
    return env, service, rev, source


def test_only_unique_matching_source_is_hashed_and_output_contains_no_identity():
    env, service, rev, source = fixture()
    def reader(args):
        return service if args[1] == "services" else rev
    result = compare_source(env, reader=reader)
    binding = {"platform": "firstrade", "service_name": "synthetic-service", "strategy_profile": "synthetic-profile",
               "account_scope": "live", "account_selector": "synthetic-account", "deployment_selector": "synthetic-deployment",
               "source_binding_id": source, "target_id": "synthetic-target"}
    expected = {"binding": binding, "domain": "qsl.firstrade-daily-binding.v1", "nonce": "a" * 64}
    assert result == hashlib.sha256(json.dumps(expected, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    assert "synthetic-account" not in result and source not in result


@pytest.mark.parametrize("case", ["bad_nonce", "wrong_source", "wrong_target", "ambiguous_traffic"])
def test_changed_identity_or_unavailable_source_is_not_hashed(case):
    env, service, rev, _ = fixture()
    if case == "bad_nonce": env["RUNTIME_DAILY_COMPARE_NONCE"] = "x"
    if case == "wrong_source": env["FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID"] = "b" * 64
    if case == "wrong_target": env["RUNTIME_TARGET_JSON"] = "{}"
    if case == "ambiguous_traffic": service["status"]["traffic"] = []
    def reader(args):
        return service if args[1] == "services" else rev
    with pytest.raises(ValueError): compare_source(env, reader=reader)
