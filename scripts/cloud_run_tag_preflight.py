"""Offline exact-name preflight; never allocate tags or authorize traffic."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._vendor.cloud_run_tags import CONTRACT_VERSION, TAGGED_URL_BUDGET, headroom

DIAGNOSTIC_TAG = "cbd"
MIN_DIAGNOSTIC_TAG_LENGTH = 3


def validate_service(service_name: str) -> int:
    """Validate the original identity before any formatting or masking."""
    if (not isinstance(service_name, str) or len(service_name) > 63
            or not re.fullmatch(r"[a-z](?:[a-z0-9-]*[a-z0-9])?", service_name)):
        raise ValueError("INVALID_SERVICE_NAME")
    return headroom(service_name)


def validate_diagnostic(service_name: str, tag: str = DIAGNOSTIC_TAG) -> None:
    room = validate_service(service_name)
    if tag != DIAGNOSTIC_TAG:
        raise ValueError("diagnostic_tag_mismatch")
    if len(tag) < MIN_DIAGNOSTIC_TAG_LENGTH:
        raise ValueError("diagnostic_tag_too_short")
    # A shortened tag would break the fixed diagnostic URL/readback contract.
    # Refuse even when the general allocator could fit a shorter tag.
    if len(tag) > room:
        raise ValueError("diagnostic_tag_name_budget_exceeded")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-name", required=True)
    args = parser.parse_args()
    try:
        validate_service(args.service_name)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("cloud_run_service_preflight_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
