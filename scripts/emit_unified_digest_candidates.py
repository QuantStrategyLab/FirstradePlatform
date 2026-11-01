"""Project existing private reports; no service or broker invocation."""
from __future__ import annotations
from contextlib import redirect_stderr, redirect_stdout
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import os
from notifications.unified import CandidateStorageError, emit_candidates  # noqa: E402


# Exact local codes only. Never print provider messages, exception class names,
# binding values, or object locators, even when they resemble a known code.
_ERROR_STAGES = {
    **dict.fromkeys((
        "candidate_bindings_invalid", "candidate_binding_mode_conflict",
        "candidate_target_enablement_unknown", "candidate_target_invalid",
        "candidate_store_identity_missing", "candidate_identity_missing",
        "candidate_store_configuration_missing",
    ), "binding_validation"),
    **dict.fromkeys((
        "candidate_archive_read_failed", "candidate_archive_budget_exceeded",
        "candidate_source_locator_mismatch", "candidate_archive_time_missing",
    ), "archive_read"),
    "candidate_source_absent": "source_selection",
    "candidate_due_source_absent": "schedule_evaluation",
    **dict.fromkeys((
        "candidate_identity_or_time_missing", "candidate_source_identity_mismatch",
        "candidate_source_invalid", "candidate_source_time_invalid",
        "candidate_source_incomplete", "candidate_run_id_missing",
        "candidate_duplicate_run", "candidate_plugin_observations_invalid",
    ), "projection"),
    **dict.fromkeys((
        "candidate_legacy_aggregate_requires_archive_refresh",
        "candidate_plugin_observations_conflict", "candidate_notification_event_conflict",
        "candidate_identity_conflict", "candidate_event_source_run_missing",
    ), "candidate_merge"),
    "candidate_concurrent_update": "candidate_write",
}
_STORAGE_CATEGORIES = frozenset({
    "permission_denied", "generation_conflict", "timeout_unknown",
    "serialization_invalid", "storage_unknown",
})


def _failure_diagnostic(error, stage):
    if isinstance(error, CandidateStorageError):
        stage = error.stage if error.stage in ("candidate_read", "candidate_write") else stage
        category = error.category
        code = category if type(category) is str and category in _STORAGE_CATEGORIES else "storage_unknown"
        return {"stage": stage, "error_code": code, "error_category": code}
    code = error.args[0] if len(error.args) == 1 and type(error.args[0]) is str else None
    if isinstance(error, (ValueError, RuntimeError)) and code in _ERROR_STAGES:
        return {"stage": _ERROR_STAGES[code], "error_code": code}
    if isinstance(error, json.JSONDecodeError):
        code = "json_invalid"
    elif stage == "output_serialization" and isinstance(error, (TypeError, ValueError)):
        code = "serialization_invalid"
    else:
        code = "unexpected_error"
    return {"stage": stage, "error_code": code}


def _check_binding_modes(env):
    raw = env.get("DIGEST_TARGET_BINDINGS_JSON")
    bindings = json.loads(raw) if raw else []
    if not isinstance(bindings, list) or not all(isinstance(item, dict) for item in bindings):
        raise ValueError("candidate_bindings_invalid")
    mode = str(env.get("QSL_ROUTINE_NOTIFICATION_MODE") or "").strip().lower()
    for binding in bindings:
        override = binding.get("QSL_ROUTINE_NOTIFICATION_MODE")
        if override is not None and str(override).strip().lower() != mode:
            raise ValueError("candidate_binding_mode_conflict")


def main():
    stage = "binding_validation"
    try:
        _check_binding_modes(os.environ)
        stage = "projection"
        result = emit_candidates(os.environ)
        stage = "output_serialization"
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        diagnostic = _failure_diagnostic(error, stage)
        print(json.dumps({"status": "candidate_projection_failed", "uploaded": False, **diagnostic}))
        from notifications.unified import alert_text, coverage_locale
        from scripts.execution_report_heartbeat import _send_telegram
        marker = os.environ.get("RUNTIME_HEARTBEAT_COVERAGE_ACK_PATH")
        try:
            attempted = bool(marker and Path(marker).read_text().strip() in {"attempted", "acknowledged"})
        except OSError:
            attempted = False
        if not attempted:
            # The legacy sender may print raw Secret Manager errors. Preserve its
            # delivery attempt without exposing that context or masking the cause.
            with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
                try:
                    delivered = _send_telegram(alert_text("candidate_write_failure", coverage_locale()))
                except Exception:
                    delivered = False
            if not delivered:
                print(json.dumps({"stage": "coverage_alert", "error_code": "alert_delivery_unconfirmed"}),
                      file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
