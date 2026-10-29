from pathlib import Path


def test_natural_and_manual_daily_sources_use_only_masked_target_secret():
    root = Path(__file__).resolve().parents[1]
    scheduled = (root / ".github/workflows/execution-report-heartbeat.yml").read_text().split("  runtime_daily:\n", 1)[1]
    manual = (root / ".github/workflows/publish-runtime-daily-once.yml").read_text()
    for workflow in (scheduled, manual):
        assert "RUNTIME_TARGET_JSON: ${{ secrets.RUNTIME_TARGET_JSON }}" in workflow
        assert "vars.RUNTIME_TARGET_JSON" not in workflow
    assert "github.event_name == 'schedule'" in scheduled
    assert "github.run_attempt == 1" in scheduled
    assert "scripts.publish_runtime_daily" in scheduled
    assert "RUNTIME_DAILY_BUSINESS_DATE" in scheduled
