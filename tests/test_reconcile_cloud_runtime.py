from __future__ import annotations

import json
import subprocess
import unittest

from scripts import reconcile_cloud_runtime as reconciler


def _completed(command: list[str], stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


class ReconcileCloudRuntimeTest(unittest.TestCase):
    def test_resolve_context_prefers_cloud_run_service_and_first_target(self):
        ctx, plan = reconciler._resolve_context(
            {
                "GCP_PROJECT_ID": "firstradequant",
                "CLOUD_RUN_SERVICE": "firstrade-platform-service",
                "CLOUD_RUN_REGION": "us-central1",
                "SYNC_PLAN_JSON": json.dumps(
                    {
                        "targets": [
                            {
                                "service_name": "firstrade-platform-service",
                                "region": "asia-east1",
                            }
                        ]
                    }
                ),
            }
        )

        self.assertEqual(ctx.project_id, "firstradequant")
        self.assertEqual(ctx.service_name, "firstrade-platform-service")
        self.assertEqual(ctx.region, "us-central1")
        self.assertEqual(ctx.scheduler_location, "us-central1")
        self.assertEqual(plan["targets"][0]["region"], "asia-east1")

    def test_reconcile_traffic_updates_latest_ready_revision_and_checks_commit_sha(self):
        env = {
            "GCP_PROJECT_ID": "firstradequant",
            "CLOUD_RUN_SERVICE": "firstrade-platform-service",
            "CLOUD_RUN_REGION": "us-central1",
            "GITHUB_SHA": "abc123",
            "SYNC_PLAN_JSON": json.dumps(
                {"targets": [{"service_name": "firstrade-platform-service"}]}
            ),
        }
        service_state = {
            "status": {
                "latestReadyRevisionName": "firstrade-platform-service-00002",
                "traffic": [{"revisionName": "firstrade-platform-service-00001", "percent": 100}],
            }
        }
        revision_state = {
            "metadata": {"labels": {"commit-sha": "abc123"}},
        }
        calls: list[list[str]] = []

        def fake_run_gcloud(command: list[str]):
            calls.append(command)
            if command[:4] == ["run", "services", "describe", "firstrade-platform-service"]:
                return _completed(command, stdout=json.dumps(service_state))
            if command[:4] == ["run", "revisions", "describe", "firstrade-platform-service-00002"]:
                return _completed(command, stdout=json.dumps(revision_state))
            if command[:4] == ["run", "services", "update-traffic", "firstrade-platform-service"]:
                service_state["status"]["traffic"] = [
                    {"revisionName": "firstrade-platform-service-00002", "percent": 100}
                ]
                return _completed(command, stdout="")
            raise AssertionError(f"Unexpected gcloud command: {command}")

        reconciler.reconcile_traffic(env, run_gcloud=fake_run_gcloud)

        self.assertEqual(
            calls,
            [
                ["run", "services", "describe", "firstrade-platform-service", "--project", "firstradequant", "--region", "us-central1", "--format=json"],
                ["run", "revisions", "describe", "firstrade-platform-service-00002", "--project", "firstradequant", "--region", "us-central1", "--format=json"],
                ["run", "services", "update-traffic", "firstrade-platform-service", "--project", "firstradequant", "--region", "us-central1", "--to-revisions", "firstrade-platform-service-00002=100", "--quiet"],
                ["run", "services", "describe", "firstrade-platform-service", "--project", "firstradequant", "--region", "us-central1", "--format=json"],
            ],
        )

    def test_reconcile_traffic_rejects_revision_with_wrong_commit_sha(self):
        env = {
            "GCP_PROJECT_ID": "firstradequant",
            "CLOUD_RUN_SERVICE": "firstrade-platform-service",
            "CLOUD_RUN_REGION": "us-central1",
            "GITHUB_SHA": "abc123",
            "SYNC_PLAN_JSON": json.dumps(
                {"targets": [{"service_name": "firstrade-platform-service"}]}
            ),
        }
        service_state = {
            "status": {
                "latestReadyRevisionName": "firstrade-platform-service-00002",
                "traffic": [{"revisionName": "firstrade-platform-service-00002", "percent": 100}],
            }
        }
        revision_state = {
            "metadata": {"labels": {"commit-sha": "deadbeef"}},
        }

        def fake_run_gcloud(command: list[str]):
            if command[:4] == ["run", "services", "describe", "firstrade-platform-service"]:
                return _completed(command, stdout=json.dumps(service_state))
            if command[:4] == ["run", "revisions", "describe", "firstrade-platform-service-00002"]:
                return _completed(command, stdout=json.dumps(revision_state))
            raise AssertionError(f"Unexpected gcloud command: {command}")

        with self.assertRaisesRegex(RuntimeError, "commit-sha"):
            reconciler.reconcile_traffic(env, run_gcloud=fake_run_gcloud)

    def test_cleanup_legacy_scheduler_jobs_only_targets_explicit_session_check_jobs(self):
        env = {
            "GCP_PROJECT_ID": "firstradequant",
            "CLOUD_RUN_SERVICE": "firstrade-platform-service",
            "CLOUD_RUN_REGION": "us-central1",
            "CLOUD_SCHEDULER_LOCATION": "us-central1",
            "SYNC_PLAN_JSON": json.dumps(
                {"targets": [{"service_name": "firstrade-platform-service"}]}
            ),
        }
        calls: list[list[str]] = []
        existing_jobs = {
            "firstrade-platform-service-session-check-scheduler",
            "firstrade-platform-session-check-scheduler",
        }

        def fake_run_gcloud(command: list[str]):
            calls.append(command)
            if command[:4] == ["scheduler", "jobs", "describe", "firstrade-platform-service-session-check-scheduler"]:
                return _completed(command, stdout="{}") if command[3] in existing_jobs else _completed(command, returncode=1)
            if command[:4] == ["scheduler", "jobs", "describe", "firstrade-platform-session-check-scheduler"]:
                return _completed(command, stdout="{}")
            if command[:4] == ["scheduler", "jobs", "delete", "firstrade-platform-service-session-check-scheduler"]:
                existing_jobs.discard("firstrade-platform-service-session-check-scheduler")
                return _completed(command, stdout="")
            if command[:4] == ["scheduler", "jobs", "delete", "firstrade-platform-session-check-scheduler"]:
                existing_jobs.discard("firstrade-platform-session-check-scheduler")
                return _completed(command, stdout="")
            raise AssertionError(f"Unexpected gcloud command: {command}")

        reconciler.cleanup_legacy_scheduler_jobs(env, run_gcloud=fake_run_gcloud)

        self.assertIn(
            ["scheduler", "jobs", "describe", "firstrade-platform-service-session-check-scheduler", "--project", "firstradequant", "--location", "us-central1"],
            calls,
        )
        self.assertIn(
            ["scheduler", "jobs", "describe", "firstrade-platform-session-check-scheduler", "--project", "firstradequant", "--location", "us-central1"],
            calls,
        )
        self.assertIn(
            ["scheduler", "jobs", "delete", "firstrade-platform-service-session-check-scheduler", "--project", "firstradequant", "--location", "us-central1", "--quiet"],
            calls,
        )
        self.assertIn(
            ["scheduler", "jobs", "delete", "firstrade-platform-session-check-scheduler", "--project", "firstradequant", "--location", "us-central1", "--quiet"],
            calls,
        )
        self.assertFalse(any("probe" in " ".join(command) or "precheck" in " ".join(command) for command in calls))


if __name__ == "__main__":
    unittest.main()
