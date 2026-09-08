from __future__ import annotations

import unittest

from application.execution_receipt_adapter import (
    attach_strategy_result_execution_receipt,
    attach_unknown_failure_execution_receipt,
)


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
    def test_legacy_unattested_results_do_not_fabricate_receipts(self) -> None:
        for attach in (
            lambda report: attach_strategy_result_execution_receipt(report, {}, dry_run=False),
            attach_unknown_failure_execution_receipt,
        ):
            report = _report()
            report["runtime_release_receipt"] = {"attestation_state": "legacy_unattested"}
            self.assertIs(attach(report), report)
            self.assertNotIn("execution_receipt", report)

    def test_invalid_attested_revision_still_fails(self) -> None:
        for revision in (None, "abc1234", "A" * 40):
            for attach in (
                lambda report: attach_strategy_result_execution_receipt(report, {}, dry_run=False),
                attach_unknown_failure_execution_receipt,
            ):
                report = _report()
                report["runtime_release_receipt"]["strategy_release"]["strategy_revision"] = revision
                with self.assertRaisesRegex(ValueError, "strategy_revision"):
                    attach(report)

    def test_missing_attestation_is_not_assumed_legacy(self) -> None:
        report = _report()
        del report["runtime_release_receipt"]
        with self.assertRaisesRegex(ValueError, "strategy_revision"):
            attach_unknown_failure_execution_receipt(report)

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
