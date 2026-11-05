"""Pure preflight excerpt from QuantPlatformKit operators/cloud_run_tags.py.

MIT License, Copyright (c) 2026 QuantStrategyLab.
See README.md for provenance. Allocation and traffic APIs are intentionally absent.
"""

CONTRACT_VERSION = 'cloud-run-tags/v1'

TAGGED_URL_BUDGET = 46

def headroom(service_name: str) -> int:
    """Measure the exact original name, before any display masking."""
    return TAGGED_URL_BUDGET - len(service_name)
