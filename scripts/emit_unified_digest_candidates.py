"""Project existing private reports; no service or broker invocation."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import os
from notifications.unified import emit_candidates  # noqa: E402


def main():
    try:
        print(json.dumps(emit_candidates(os.environ), sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"status": "candidate_projection_failed", "uploaded": False}))
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
