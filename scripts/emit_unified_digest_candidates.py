"""Project existing private reports; no service or broker invocation."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import os
from notifications.unified import emit_candidates, enabled  # noqa: E402


def main():
    if not enabled():
        from notifications.digest_snapshot.digest_archive import main as legacy_main
        return legacy_main()
    try:
        print(json.dumps(emit_candidates(os.environ), sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"status": "candidate_projection_failed", "uploaded": False}))
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
