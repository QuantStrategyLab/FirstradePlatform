"""Default-off E3 reconciliation groundwork for Firstrade.

This module owns no broker client and is intentionally not wired into any
runtime or order path.  A future, explicitly authorized read-only collector
must be injected before reconciliation evidence can be created.
"""

from __future__ import annotations

from collections.abc import Callable

from quant_platform_kit.common.broker_reconciliation import BrokerReconciliationEvidence


class FirstradeReconciliationUnavailable(RuntimeError):
    """Raised when read-only E3 evidence collection is unavailable or invalid."""


def collect_broker_reconciliation_evidence(
    *,
    collector: Callable[[], BrokerReconciliationEvidence] | None = None,
) -> BrokerReconciliationEvidence:
    """Return injected QPK evidence without constructing broker or order context.

    The default keeps E3 disabled.  This fail-closed boundary intentionally
    precedes any future collector wiring, so ordinary runtime code cannot
    silently create a broker session or execution port through this helper.
    """

    if not callable(collector):
        raise FirstradeReconciliationUnavailable("Firstrade reconciliation collector is not configured.")
    evidence = collector()
    if not isinstance(evidence, BrokerReconciliationEvidence):
        raise FirstradeReconciliationUnavailable("Firstrade reconciliation collector must return QPK evidence.")
    if evidence.platform_id != "firstrade":
        raise FirstradeReconciliationUnavailable("Firstrade reconciliation evidence has the wrong platform.")
    return evidence
