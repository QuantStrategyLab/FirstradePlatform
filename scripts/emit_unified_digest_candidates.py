"""Project existing private reports; no service or broker invocation."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import os
from notifications.unified import emit_candidates  # noqa: E402


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
    try:
        _check_binding_modes(os.environ)
        print(json.dumps(emit_candidates(os.environ), sort_keys=True))
        return 0
    except Exception as error:
        from notifications.unified import CandidateStorageError
        diagnostic = ({"stage": error.stage, "error_category": error.category}
                      if isinstance(error, CandidateStorageError) else {})
        print(json.dumps({"status": "candidate_projection_failed", "uploaded": False, **diagnostic}))
        from notifications.unified import alert_text, coverage_locale
        from scripts.execution_report_heartbeat import _send_telegram
        marker = os.environ.get("RUNTIME_HEARTBEAT_COVERAGE_ACK_PATH")
        try:
            attempted = bool(marker and Path(marker).read_text().strip() in {"attempted", "acknowledged"})
        except OSError:
            attempted = False
        if not attempted:
            _send_telegram(alert_text("candidate_write_failure", coverage_locale()))
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
