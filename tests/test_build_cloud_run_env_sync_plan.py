from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


SYNC_PLAN_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "build_cloud_run_env_sync_plan.py"
)


def runtime_target_json(
    strategy_profile: str,
    *,
    dry_run_only: bool = True,
    platform_id: str = "firstrade",
    service_name: str | None = None,
) -> str:
    payload: dict[str, object] = {
        "platform_id": platform_id,
        "strategy_profile": strategy_profile,
        "dry_run_only": dry_run_only,
        "execution_mode": "paper" if dry_run_only else "live",
    }
    if service_name is not None:
        payload["service_name"] = service_name
    return json.dumps(payload, separators=(",", ":"))


def test_build_cloud_run_env_sync_plan_legacy_mode_without_telegram():
    env = {
        **os.environ,
        "CLOUD_RUN_SERVICE": "firstrade-platform-service",
        "NOTIFY_LANG": "zh",
        "RUNTIME_TARGET_JSON": runtime_target_json(
            "tqqq_growth_income",
            service_name="firstrade-platform-service",
        ),
        "FIRSTRADE_MIN_RESERVED_CASH_USD": "250",
        "GLOBAL_TELEGRAM_CHAT_ID": "",
    }

    result = subprocess.run(
        [sys.executable, str(SYNC_PLAN_SCRIPT_PATH), "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    plan = json.loads(result.stdout)
    assert plan["mode"] == "legacy"
    target = plan["targets"][0]
    assert target["service_name"] == "firstrade-platform-service"
    assert target["strategy_profile"] == "tqqq_growth_income"
    assert target["env"]["NOTIFY_LANG"] == "zh"
    assert target["env"]["STRATEGY_PROFILE"] == "tqqq_growth_income"
    assert target["env"]["FIRSTRADE_DRY_RUN_ONLY"] == "true"
    assert target["env"]["FIRSTRADE_MIN_RESERVED_CASH_USD"] == "250"
    assert "GLOBAL_TELEGRAM_CHAT_ID" not in target["env"]
    assert "GLOBAL_TELEGRAM_CHAT_ID" in target["remove_env_vars"]
    assert "FIRSTRADE_FEATURE_SNAPSHOT_PATH" in target["remove_env_vars"]
    assert target["scheduler"]["timezone"] == "America/New_York"


def test_build_cloud_run_env_sync_plan_carries_balance_facts_config_but_not_token_value():
    facts_config = {
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED": "false",
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_URL": "https://qsl-strategy-switch-console.pigbibi.workers.dev/api/account-facts/sync",
        "FIRSTRADE_ACCOUNT_FACTS_TARGET_ID": "synthetic-target",
        "FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID": "a" * 64,
        "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_KEY": "synthetic-account-key",
        "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE": "US",
    }
    env = {
        **os.environ,
        "CLOUD_RUN_SERVICE": "firstrade-platform-service",
        "NOTIFY_LANG": "en",
        "RUNTIME_TARGET_JSON": runtime_target_json(
            "tqqq_growth_income",
            service_name="firstrade-platform-service",
        ),
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN": "synthetic-token-must-not-enter-plan",
        **facts_config,
    }

    result = subprocess.run(
        [sys.executable, str(SYNC_PLAN_SCRIPT_PATH), "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    target = json.loads(result.stdout)["targets"][0]
    for name, value in facts_config.items():
        assert target["env"][name] == value
    assert "FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN" not in target["env"]
    assert "synthetic-token-must-not-enter-plan" not in result.stdout


def test_per_service_facts_config_fallback_applies_only_to_current_firstrade_target():
    current_service = "firstrade-platform-service"
    sibling_service = "firstrade-platform-paper-service"
    targets = [
        {
            "service": service,
            "strategy_profile": "tqqq_growth_income",
            "runtime_target": json.loads(
                runtime_target_json("tqqq_growth_income", service_name=service)
            ),
        }
        for service in (current_service, sibling_service)
    ]
    env = {
        **os.environ,
        "CLOUD_RUN_SERVICE": current_service,
        "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps(
            {"defaults": {"NOTIFY_LANG": "en"}, "targets": targets}
        ),
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED": "false",
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_URL": "https://qsl-strategy-switch-console.pigbibi.workers.dev/api/account-facts/sync",
        "FIRSTRADE_ACCOUNT_FACTS_TARGET_ID": "synthetic-target",
        "FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID": "b" * 64,
        "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_KEY": "synthetic-account-key",
        "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE": "US",
    }

    result = subprocess.run(
        [sys.executable, str(SYNC_PLAN_SCRIPT_PATH), "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    by_service = {item["service_name"]: item for item in json.loads(result.stdout)["targets"]}
    facts_names = (
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED",
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_URL",
        "FIRSTRADE_ACCOUNT_FACTS_TARGET_ID",
        "FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID",
        "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_KEY",
        "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE",
    )
    for name in facts_names:
        assert name in by_service[current_service]["env"]
        assert name not in by_service[sibling_service]["env"]
        assert name in by_service[sibling_service]["remove_env_vars"]


def test_build_cloud_run_env_sync_plan_requires_snapshot_for_snapshot_backed_profile():
    env = {
        **os.environ,
        "CLOUD_RUN_SERVICE": "firstrade-platform-service",
        "NOTIFY_LANG": "en",
        "RUNTIME_TARGET_JSON": runtime_target_json(
            "global_etf_rotation",
            service_name="firstrade-platform-service",
        ),
        "FIRSTRADE_FEATURE_SNAPSHOT_PATH": "gs://stale-paper/snapshot.csv",
    }

    result = subprocess.run(
        [sys.executable, str(SYNC_PLAN_SCRIPT_PATH), "--json"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "firstrade-platform-service:FIRSTRADE_FEATURE_SNAPSHOT_MANIFEST_PATH" in result.stderr
    assert "gs://stale-paper/snapshot.csv" not in result.stderr


def test_build_cloud_run_env_sync_plan_skips_snapshot_requirements_when_disabled():
    payload = {
        "defaults": {"NOTIFY_LANG": "en"},
        "targets": [
            {
                "service": "firstrade-platform-service",
                "runtime_target_enabled": "false",
                "runtime_target": json.loads(
                    runtime_target_json(
                        "global_etf_rotation",
                        service_name="firstrade-platform-service",
                    )
                ),
            }
        ],
    }
    env = {
        **os.environ,
        "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps(payload),
        "FIRSTRADE_FEATURE_SNAPSHOT_PATH": "gs://stale-paper/snapshot.csv",
        "FIRSTRADE_FEATURE_SNAPSHOT_MANIFEST_PATH": "gs://stale-paper/snapshot.csv.manifest.json",
    }

    result = subprocess.run(
        [sys.executable, str(SYNC_PLAN_SCRIPT_PATH), "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    plan = json.loads(result.stdout)
    assert plan["mode"] == "per_service"
    target = plan["targets"][0]
    assert target["env"]["RUNTIME_TARGET_ENABLED"] == "false"
    assert "FIRSTRADE_FEATURE_SNAPSHOT_PATH" not in target["env"]
    assert "FIRSTRADE_FEATURE_SNAPSHOT_MANIFEST_PATH" not in target["env"]
    assert "FIRSTRADE_FEATURE_SNAPSHOT_PATH" in target["remove_env_vars"]
    assert "gs://stale-paper/snapshot.csv" not in result.stdout


def test_build_cloud_run_env_sync_plan_requires_notify_lang():
    env = {
        **os.environ,
        "CLOUD_RUN_SERVICE": "firstrade-platform-service",
        "RUNTIME_TARGET_JSON": runtime_target_json(
            "tqqq_growth_income",
            service_name="firstrade-platform-service",
        ),
    }
    env.pop("NOTIFY_LANG", None)

    result = subprocess.run(
        [sys.executable, str(SYNC_PLAN_SCRIPT_PATH), "--json"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "firstrade-platform-service:NOTIFY_LANG" in result.stderr
