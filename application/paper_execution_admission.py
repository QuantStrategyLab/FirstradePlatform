"""Fail-closed, opt-in PAPER admission for Firstrade dry-run requests.

The shared QPK contract verifies an immutable execution command, its embedded
deterministic-risk receipt, and the release currently loaded by the runtime.
This adapter intentionally only turns that pure result into a redacted HTTP
audit record.  It neither creates commands nor reaches a broker, account,
strategy, deployment, scheduler, or persistence service.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from quant_platform_kit.common.execution_commands import ExecutionCommand
from quant_platform_kit.common.paper_execution_admission import (
    PaperExecutionAdmissionDecision,
    evaluate_paper_execution_admission,
)


PAPER_ADMISSION_ENABLED_ENV = "QSL_PAPER_ADMISSION_ENABLED"
PAPER_EXECUTION_COMMAND_ENV = "QSL_PAPER_EXECUTION_COMMAND_JSON"
PAPER_EXECUTION_ADMISSION_AUDIT_SCHEMA_VERSION = "firstrade_paper_execution_admission_audit.v1"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})
_COMMAND_MISSING = "paper_execution_command_missing"
_COMMAND_INVALID = "paper_execution_command_invalid"
_PLATFORM_MISMATCH = "paper_execution_platform_mismatch"
_ADMISSION_CONFIGURATION_INVALID = "paper_execution_admission_configuration_invalid"
_ADMISSION_EVALUATION_FAILED = "paper_execution_admission_evaluation_failed"
_RUNTIME_MODE_INVALID = "paper_runtime_mode_invalid"


def paper_dry_run_admission_requested(env: Mapping[str, str | None]) -> bool:
    """Return whether this request should be checked instead of using legacy dry-run.

    The default is disabled.  A malformed non-empty enable value is treated as
    requested so the caller receives a fail-closed audit rather than silently
    falling back to the ungated legacy preview path.
    """
    raw_value = env.get(PAPER_ADMISSION_ENABLED_ENV)
    if raw_value is None:
        return False
    return str(raw_value).strip().lower() not in _FALSE_VALUES


def _enabled(env: Mapping[str, str | None]) -> bool:
    return str(env.get(PAPER_ADMISSION_ENABLED_ENV) or "").strip().lower() in _TRUE_VALUES


def _audit(
    *,
    disposition: str,
    findings: tuple[str, ...] | list[str],
    command_id: str | None = None,
    receipt_sha256: str | None = None,
) -> dict[str, object]:
    admitted = disposition == "allow_new_risk" and not findings
    return {
        "schema_version": PAPER_EXECUTION_ADMISSION_AUDIT_SCHEMA_VERSION,
        "admission_enabled": True,
        "audit_color": "green" if admitted else "red",
        "status": "admitted" if admitted else "blocked",
        "command_id": command_id,
        "disposition": disposition,
        "integrity_findings": list(findings),
        "receipt_sha256": receipt_sha256,
    }


def _audit_from_decision(decision: PaperExecutionAdmissionDecision) -> dict[str, object]:
    """Return only QPK's stable decision metadata, never raw command intent."""
    return _audit(
        disposition=decision.disposition.value,
        findings=decision.integrity_findings,
        command_id=decision.command_id,
        receipt_sha256=decision.receipt_sha256,
    )


def evaluate_paper_dry_run_admission(
    *,
    runtime_target: object | None,
    env: Mapping[str, str | None],
) -> dict[str, object] | None:
    """Evaluate an optional QPK PAPER admission before any preview can start.

    ``None`` preserves the existing dry-run route while the feature is disabled.
    Once requested, every malformed or incomplete input produces a red audit
    record and the caller must avoid invoking the strategy cycle.
    """
    if not paper_dry_run_admission_requested(env):
        return None
    if not _enabled(env):
        return _audit(
            disposition="halted",
            findings=(_ADMISSION_CONFIGURATION_INVALID,),
        )

    raw_command = env.get(PAPER_EXECUTION_COMMAND_ENV)
    if not raw_command:
        return _audit(disposition="halted", findings=(_COMMAND_MISSING,))
    try:
        payload = json.loads(raw_command)
        if not isinstance(payload, Mapping):
            raise ValueError("command payload must be an object")
        command = ExecutionCommand.from_dict(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _audit(disposition="halted", findings=(_COMMAND_INVALID,))

    if command.platform != "firstrade":
        return _audit(
            disposition="halted",
            findings=(_PLATFORM_MISMATCH,),
            command_id=command.command_id,
        )
    if getattr(runtime_target, "dry_run_only", None) is not True:
        return _audit(
            disposition="halted",
            findings=(_RUNTIME_MODE_INVALID,),
            command_id=command.command_id,
        )
    expected_release = getattr(runtime_target, "strategy_release", None)
    try:
        decision = evaluate_paper_execution_admission(
            command=command,
            expected_strategy_release=expected_release,
        )
    except (TypeError, ValueError):
        return _audit(
            disposition="halted",
            findings=(_ADMISSION_EVALUATION_FAILED,),
            command_id=command.command_id,
        )
    return _audit_from_decision(decision)


__all__ = [
    "PAPER_ADMISSION_ENABLED_ENV",
    "PAPER_EXECUTION_ADMISSION_AUDIT_SCHEMA_VERSION",
    "PAPER_EXECUTION_COMMAND_ENV",
    "evaluate_paper_dry_run_admission",
    "paper_dry_run_admission_requested",
]
