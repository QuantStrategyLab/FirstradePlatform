"""Project existing private reports; no service or broker invocation."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import os
from notifications.unified import emit_candidates, enabled  # noqa: E402


def _check_binding_modes(env):
    if env.get("QSL_ROUTINE_NOTIFICATION_MODE") != "daily_digest":
        return
    raw = env.get("DIGEST_TARGET_BINDINGS_JSON", "").strip()
    if not raw:
        return
    bindings = json.loads(raw)
    if not isinstance(bindings, list) or any(not isinstance(row, dict) for row in bindings):
        raise ValueError("candidate_bindings_invalid")
    if any(row.get("QSL_ROUTINE_NOTIFICATION_MODE", "daily_digest") != "daily_digest" for row in bindings):
        raise ValueError("candidate_binding_mode_override")


def main():
    try:
        _check_binding_modes(os.environ)
        if not enabled():
            from quant_platform_kit.notifications.digest_archive import main as legacy_main
            return legacy_main()
        print(json.dumps(emit_candidates(os.environ), sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"status": "candidate_projection_failed", "uploaded": False}))
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
