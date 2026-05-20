#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategy_registry import get_platform_profile_status_matrix  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print Firstrade strategy profile status.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    rows = get_platform_profile_status_matrix()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    headers = [
        "Canonical profile",
        "Display name",
        "Eligible",
        "Enabled",
        "Domain",
    ]
    print(" | ".join(headers))
    print(" | ".join("---" for _ in headers))
    for row in rows:
        print(
            " | ".join(
                [
                    str(row["canonical_profile"]),
                    str(row["display_name"]),
                    "Yes" if row["eligible"] else "No",
                    "Yes" if row["enabled"] else "No",
                    str(row["domain"]),
                ]
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
