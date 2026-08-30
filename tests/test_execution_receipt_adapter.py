from __future__ import annotations

import unittest

from application.execution_receipt_adapter import attach_strategy_result_execution_receipt


REVISION = "a" * 40


def _report() -> dict[str, object]:
    return {
        "platform": "firstrade",
        "strategy_profile": "ibit_smart_dca",
        "dry_run": False,
        "runtime_target": {"execution_mode": "live"},
        "runtime_release_receipt": {
            "attestation_state": "self_attested",
            "strategy_release": {"strategy_revision": REVISION},
        },
    }


class ExecutionReceiptAdapterTest(unittest.TestCase):
    def test_submission_is_not_reported_as_a_fill(self) -> None:
        report = _report()

        attach_strategy_result_execution_receipt(
            report,
            {
                "strategy_run_stage": "SUBMITTED",
                "action_done": True,
                "submitted_orders": [{"symbol": "IBIT"}],
            },
            dry_run=False,
        )

        self.assertEqual(report["execution_receipt"]["outcome"], "submitted")
        self.assertEqual(report["execution_receipt"]["broker_confirmation"], "not_observed")

    def test_pending_prior_submission_requires_reconciliation(self) -> None:
        report = _report()

        attach_strategy_result_execution_receipt(
            report,
            {"strategy_run_stage": "PENDING_RECONCILIATION"},
            dry_run=False,
        )

        self.assertEqual(report["execution_receipt"]["outcome"], "reconciliation_required")

    def test_funding_block_is_not_a_broker_failure(self) -> None:
        report = _report()

        attach_strategy_result_execution_receipt(
            report,
            {"strategy_run_stage": "FUNDING_BLOCKED", "funding_blocked": True},
            dry_run=False,
        )

        self.assertEqual(report["execution_receipt"]["outcome"], "risk_blocked")

    def test_dry_run_never_claims_submission(self) -> None:
        report = _report()
        report["dry_run"] = True

        attach_strategy_result_execution_receipt(
            report,
            {"strategy_run_stage": "SUBMITTED", "action_done": True},
            dry_run=True,
        )

        self.assertEqual(report["execution_receipt"]["outcome"], "no_action")
