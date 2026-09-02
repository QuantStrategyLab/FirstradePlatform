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
