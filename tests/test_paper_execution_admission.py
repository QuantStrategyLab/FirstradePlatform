from __future__ import annotations

import json

from application.paper_execution_admission import (
    PAPER_ADMISSION_ENABLED_ENV,
    PAPER_EXECUTION_COMMAND_ENV,
    evaluate_paper_dry_run_admission,
)
from quant_platform_kit.common.execution_commands import ExecutionCommand
from quant_platform_kit.common.paper_execution_admission import build_paper_risk_admission_receipt
from quant_platform_kit.common.runtime_target import build_runtime_target


def _release() -> dict[str, str]:
    return {
        "release_id": "soxl-p2-v3.20260825",
        "manifest_sha256": "a" * 64,
        "strategy_revision": "soxl-p2-v3",
        "config_sha256": "b" * 64,
        "risk_policy_sha256": "c" * 64,
        "evidence_sha256": "d" * 64,
        "plugin_bundle_sha256": "e" * 64,
        "effective_session": "2026-08-25",
    }


def _runtime_target():
    return build_runtime_target(
        platform_id="firstrade",
        strategy_profile="soxl_soxx_trend_income",
        dry_run_only=True,
        strategy_release=_release(),
    )


def _command(
    *,
    platform: str = "firstrade",
    include_receipt: bool = True,
    disposition: str = "allow_new_risk",
    reason_codes: tuple[str, ...] = (),
    command_decision_digest: str = "f" * 64,
) -> dict[str, object]:
    release = _release()
    intent: dict[str, object] = {"strategy_release": release, "targets": {"SOXL": 0.2}}
    if include_receipt:
        receipt = build_paper_risk_admission_receipt(
            strategy_profile="soxl_soxx_trend_income",
            release_id=release["release_id"],
            risk_policy_sha256=release["risk_policy_sha256"],
            decision_digest="f" * 64,
            effective_session="2026-08-25",
            disposition=disposition,
            reason_codes=reason_codes,
        )
        intent["paper_risk_admission_receipt"] = receipt.to_dict()
    return ExecutionCommand.from_decision(
        platform=platform,
        account_scope="paper",
        strategy_profile="soxl_soxx_trend_income",
        execution_mode="paper",
        signal_date="2026-08-24",
        effective_date="2026-08-25",
        execution_timing_contract="next_trading_day",
        decision_digest=command_decision_digest,
        intent=intent,
        created_at="2026-08-24T20:00:00+00:00",
    ).to_dict()


def test_paper_admission_is_disabled_by_default_even_without_a_command():
    assert evaluate_paper_dry_run_admission(runtime_target=None, env={}) is None


def test_paper_admission_requires_an_immutable_command_before_preview():
    audit = evaluate_paper_dry_run_admission(
        runtime_target=_runtime_target(),
        env={PAPER_ADMISSION_ENABLED_ENV: "true"},
    )

    assert audit == {
        "schema_version": "firstrade_paper_execution_admission_audit.v1",
        "admission_enabled": True,
        "audit_color": "red",
        "status": "blocked",
        "command_id": None,
        "disposition": "halted",
        "integrity_findings": ["paper_execution_command_missing"],
        "receipt_sha256": None,
    }


def test_matching_command_receipt_and_runtime_release_admit_paper_preview():
    audit = evaluate_paper_dry_run_admission(
        runtime_target=_runtime_target(),
        env={
            PAPER_ADMISSION_ENABLED_ENV: "true",
            PAPER_EXECUTION_COMMAND_ENV: json.dumps(_command()),
        },
    )

    assert audit is not None
    assert audit["status"] == "admitted"
    assert audit["audit_color"] == "green"
    assert audit["disposition"] == "allow_new_risk"
    assert audit["integrity_findings"] == []


def test_non_firstrade_or_missing_embedded_receipt_stays_redacted_and_blocked():
    for command, finding in (
        (_command(platform="longbridge"), "paper_execution_platform_mismatch"),
        (_command(include_receipt=False), "paper_risk_admission_receipt_missing"),
    ):
        audit = evaluate_paper_dry_run_admission(
            runtime_target=_runtime_target(),
            env={
                PAPER_ADMISSION_ENABLED_ENV: "true",
                PAPER_EXECUTION_COMMAND_ENV: json.dumps(command),
            },
        )

        assert audit is not None
        assert audit["status"] == "blocked"
        assert audit["audit_color"] == "red"
        assert audit["integrity_findings"] == [finding]
        assert "targets" not in audit
        assert "account_scope" not in audit


def test_reducing_only_receipt_is_blocked_until_a_platform_can_prove_reduction():
    audit = evaluate_paper_dry_run_admission(
        runtime_target=_runtime_target(),
        env={
            PAPER_ADMISSION_ENABLED_ENV: "true",
            PAPER_EXECUTION_COMMAND_ENV: json.dumps(
                _command(
                    disposition="reducing_only",
                    reason_codes=("DAILY_LOSS_LIMIT_EXCEEDED",),
                )
            ),
        },
    )

    assert audit is not None
    assert audit["status"] == "blocked"
    assert audit["audit_color"] == "red"
    assert audit["disposition"] == "reducing_only"
    assert audit["integrity_findings"] == ["paper_risk_admission_reducing_only"]


def test_mismatched_risk_decision_digest_is_red_and_never_admitted():
    audit = evaluate_paper_dry_run_admission(
        runtime_target=_runtime_target(),
        env={
            PAPER_ADMISSION_ENABLED_ENV: "true",
            PAPER_EXECUTION_COMMAND_ENV: json.dumps(_command(command_decision_digest="e" * 64)),
        },
    )

    assert audit is not None
    assert audit["status"] == "blocked"
    assert audit["audit_color"] == "red"
    assert audit["disposition"] == "halted"
    assert audit["integrity_findings"] == ["paper_risk_admission_command_mismatch"]
