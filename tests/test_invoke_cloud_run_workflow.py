from __future__ import annotations

from pathlib import Path


def test_invoke_cloud_run_workflow_ensures_probe_and_dry_run_scheduler_bridges():
    workflow_path = Path(__file__).resolve().parents[1] / ".github/workflows/invoke-cloud-run.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "GCP_SCHEDULER_SERVICE_ACCOUNT: firstrade-platform-scheduler@firstradequant.iam.gserviceaccount.com" in workflow
    assert "ensure_invoke_bridge_job()" in workflow
    assert 'scheduler_job="${CLOUD_RUN_SERVICE}-probe-scheduler"' in workflow
    assert 'scheduler_job="${CLOUD_RUN_SERVICE}-precheck-scheduler"' in workflow
    assert 'ensure_invoke_bridge_job "${scheduler_job}" "${service_url}/probe"' in workflow
    assert 'ensure_invoke_bridge_job "${scheduler_job}" "${service_url}/dry-run"' in workflow
    assert '--schedule="0 0 1 1 *"' in workflow
