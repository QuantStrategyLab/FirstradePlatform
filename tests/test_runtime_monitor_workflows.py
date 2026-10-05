import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_execution_report_heartbeat_has_market_neutral_daily_schedule() -> None:
    workflow = (ROOT / ".github/workflows/execution-report-heartbeat.yml").read_text()

    assert 'cron: "20 22 * * *"' in workflow
    assert 'cron: "20 22 * * 1-5"' not in workflow
    assert "RUNTIME_HEARTBEAT_MARKET_AWARE:" in workflow
    assert "RUNTIME_HEARTBEAT_PUBLICATION_GRACE_MINUTES:" in workflow
    assert "RUNTIME_HEARTBEAT_SCHEDULER_LOCATION:" in workflow
    assert "CLOUD_SCHEDULER_MAIN_TIME:" in workflow
    assert "EXECUTION_REPORT_GCS_URI:" in workflow
    assert "pandas-market-calendars==5.4.0" not in workflow


def test_qpk_dependent_heartbeats_use_one_locked_uv_runtime_per_job() -> None:
    setup_uv = "astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9"
    qpk_scripts = {
        "execution-report-heartbeat.yml": ("scripts/execution_report_heartbeat.py",),
        "runtime-target-lifecycle.yml": (
            "scripts/cloud_run_runtime_guard.py",
            "scripts/execution_report_heartbeat.py",
        ),
    }

    for name, scripts in qpk_scripts.items():
        workflow = (ROOT / ".github/workflows" / name).read_text()

        assert workflow.count(setup_uv) == 1
        assert workflow.count("uv sync --frozen --no-dev") == 1
        assert "pandas-market-calendars==5.4.0" not in workflow
        assert "python -m pip install" not in workflow
        assert "actions/setup-python@" not in workflow
        for script in scripts:
            script_lines = [line for line in workflow.splitlines() if script in line]
            assert script_lines
            assert all(f"uv run --no-sync python {script}" in line for line in script_lines)


def test_lifecycle_import_failures_are_unavailable() -> None:
    workflow = (ROOT / ".github/workflows/runtime-target-lifecycle.yml").read_text()

    assert workflow.count("status=unavailable") == 2
    assert workflow.count("|import|traceback|") == 2


def test_runtime_monitor_workflows_retry_gcp_authentication() -> None:
    for name in ("execution-report-heartbeat.yml", "runtime-guard.yml"):
        workflow = (ROOT / ".github/workflows" / name).read_text()

        assert workflow.count("google-github-actions/auth@v3") == 2
        assert "id: gcp_auth_primary" in workflow
        assert "continue-on-error: true" in workflow
        assert "steps.gcp_auth_primary.outcome == 'failure'" in workflow


def test_runtime_guard_callers_use_locked_uv_runtime_before_authentication() -> None:
    workflow_root = ROOT / ".github/workflows"
    callers = sorted(
        path
        for pattern in ("*.yml", "*.yaml")
        for path in workflow_root.rglob(pattern)
        if "scripts/cloud_run_runtime_guard.py" in path.read_text()
    )

    assert callers
    setup_uv = "astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9"
    setup_python = "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1"
    guard_command = "uv run --no-sync python scripts/cloud_run_runtime_guard.py"
    setup_python_lines = []
    for path in callers:
        workflow = path.read_text()

        assert setup_uv in workflow
        assert "uv sync --frozen --no-dev" in workflow
        assert guard_command in workflow
        assert not re.search(r"pip install[^\n]*\buv\b", workflow)
        assert "actions/setup-python@v6" not in workflow
        setup_python_lines.extend(
            line for line in workflow.splitlines() if "actions/setup-python@" in line
        )
        assert all(
            guard_command in line
            for line in workflow.splitlines()
            if "scripts/cloud_run_runtime_guard.py" in line
        )
        assert workflow.index(setup_uv) < workflow.index("google-github-actions/auth@v3")
        assert workflow.index("uv sync --frozen --no-dev") < workflow.index(
            "google-github-actions/auth@v3"
        )
    assert setup_python_lines == [f"        uses: {setup_python}"]


def test_lifecycle_observes_completed_sync_regardless_of_conclusion() -> None:
    workflow = (ROOT / ".github/workflows/runtime-target-lifecycle.yml").read_text()
    sync = (ROOT / ".github/workflows/sync-cloud-run-env.yml").read_text()
    sync_name = sync.splitlines()[0].removeprefix("name: ")

    assert f'workflows: ["{sync_name}"]' in workflow
    assert "types: [completed]" in workflow
    assert "github.event.workflow_run.conclusion" not in workflow
    assert "github.event.workflow_run.head_sha" not in workflow


def test_metadata_only_dispatch_is_opt_in_and_skips_lifecycle_job() -> None:
    workflow = (ROOT / ".github/workflows/runtime-target-lifecycle.yml").read_text()
    lifecycle = workflow.split("  lifecycle:", 1)[1].split("  account_data_readiness:", 1)[0]
    metadata_job = workflow.split("  account_data_readiness:", 1)[1]

    assert "metadata_only:" in workflow
    assert "type: boolean" in workflow
    assert "default: false" in workflow
    assert "if: ${{ !(github.event_name == 'workflow_dispatch' && inputs.metadata_only) }}" in lifecycle
    assert "if: ${{ github.event_name == 'workflow_dispatch' && inputs.metadata_only }}" in metadata_job
    assert "uv sync --frozen --no-dev" not in metadata_job
    assert "scripts/inspect_account_data_readiness.py" in metadata_job
    assert "CLOUD_RUN_SERVICE: ${{ secrets.CLOUD_RUN_SERVICE }}" in metadata_job
    assert "CLOUD_RUN_REGION: ${{ vars.CLOUD_RUN_REGION }}" in metadata_job


def test_lifecycle_publishes_read_only_observation_for_exact_service() -> None:
    workflow = (ROOT / ".github/workflows/runtime-target-lifecycle.yml").read_text()
    publisher = workflow.split("- name: Publish lifecycle to the unified control plane", 1)[1]
    publisher = publisher.split("\n      - name:", 1)[0]

    for line in (
        "observe-gcp: 'true'",
        "gcp-project: ${{ env.GCP_PROJECT_ID }}",
        "cloud-run-region: ${{ env.CLOUD_RUN_REGION }}",
        "cloud-run-service: ${{ env.CLOUD_RUN_SERVICE }}",
        "scheduler-location: ${{ env.RUNTIME_HEARTBEAT_SCHEDULER_LOCATION }}",
    ):
        assert line in publisher
    assert "CLOUD_RUN_SERVICE: ${{ secrets.CLOUD_RUN_SERVICE }}" in workflow
    assert "RUNTIME_HEARTBEAT_SCHEDULER_LOCATION: ${{ vars.RUNTIME_HEARTBEAT_SCHEDULER_LOCATION || vars.CLOUD_RUN_REGION || 'us-central1' }}" in workflow
    assert "CLOUD_RUN_SERVICES" not in publisher
    assert "gcloud scheduler jobs update" not in workflow
    assert "gcloud run deploy" not in workflow


def _manual_input_block(workflow: str, name: str) -> str:
    import re
    match = re.search(rf"(?ms)^      {name}:\n(.*?)(?=^      [a-z_]+:|^  [a-z_]+:)", workflow)
    assert match is not None, f"missing workflow_dispatch input {name}"
    return match.group(1)


def _evaluate_success_notify_expression(workflow: str, event: str, explicit_input, repository_value) -> str:
    """Evaluate this bounded Actions boolean/string expression without running a workflow."""
    import ast
    import re
    expression = re.search(r"RUNTIME_HEARTBEAT_NOTIFY_ON_SUCCESS: \$\{\{ (.*?) \}\}", workflow).group(1)
    expression = expression.replace("github.event_name", repr(event))
    expression = expression.replace("inputs.notify_on_success", repr(False if explicit_input is None else explicit_input))
    expression = expression.replace("vars.RUNTIME_HEARTBEAT_NOTIFY_ON_SUCCESS", repr(repository_value or ""))
    expression = expression.replace("&&", " and ").replace("||", " or ")
    parsed = ast.parse(expression, mode="eval")
    assert all(isinstance(node, (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Compare, ast.Eq, ast.NotEq, ast.Constant)) for node in ast.walk(parsed))
    result = eval(compile(parsed, "<offline Actions expression>", "eval"), {"__builtins__": {}}, {})
    return str(result).lower() if isinstance(result, bool) else str(result)


def test_manual_healthy_notify_is_typed_and_explicitly_opt_in() -> None:
    workflow = (ROOT / ".github/workflows/execution-report-heartbeat.yml").read_text()
    block = _manual_input_block(workflow, "notify_on_success")
    assert "type: boolean" in block
    assert "default: false" in block
    assert "required: false" in block
    line = next(line for line in workflow.splitlines() if "RUNTIME_HEARTBEAT_NOTIFY_ON_SUCCESS:" in line)
    assert "inputs.notify_on_success" in line
    assert "github.event.inputs" not in line
    assert "vars.RUNTIME_HEARTBEAT_NOTIFY_ON_SUCCESS" not in line
    assert "github.event_name == 'workflow_dispatch'" in line


def test_schedule_and_manual_default_never_inherit_legacy_success_variable() -> None:
    workflow = (ROOT / ".github/workflows/execution-report-heartbeat.yml").read_text()
    for event in ("schedule", "workflow_dispatch", "repository_dispatch"):
        for explicit_input in (None, False, True):
            for repository_value in (None, "false", "true"):
                actual = _evaluate_success_notify_expression(workflow, event, explicit_input, repository_value)
                expected = "true" if event == "workflow_dispatch" and explicit_input is True else "false"
                assert actual == expected, (event, explicit_input, repository_value, actual)


def test_manual_quiet_control_preserves_existing_alert_and_report_steps() -> None:
    workflow = (ROOT / ".github/workflows/execution-report-heartbeat.yml").read_text()
    alert_block = _manual_input_block(workflow, "fail_workflow_on_alert")
    assert 'default: "true"' in alert_block
    assert "RUNTIME_HEARTBEAT_FAIL_WORKFLOW_ON_ALERT: ${{ inputs.fail_workflow_on_alert || vars.RUNTIME_HEARTBEAT_FAIL_WORKFLOW_ON_ALERT || 'true' }}" in workflow
    assert "uv run --no-sync python scripts/execution_report_heartbeat.py" in workflow
    assert "Publish read-only runtime execution evidence" in workflow
    assert 'cron: "20 22 * * *"' in workflow
