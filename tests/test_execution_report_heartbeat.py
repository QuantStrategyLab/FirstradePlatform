from __future__ import annotations

import datetime as dt

from scripts import execution_report_heartbeat as heartbeat


def test_report_globs_include_sanitized_month_segments(monkeypatch):
    monkeypatch.delenv("RUNTIME_HEARTBEAT_GCS_GLOBS", raising=False)
    monkeypatch.delenv("EXECUTION_REPORT_GCS_URI", raising=False)
    monkeypatch.delenv("RUNTIME_HEARTBEAT_GCS_URIS", raising=False)
    monkeypatch.delenv("RUNTIME_HEARTBEAT_REPORT_PLATFORM", raising=False)
    monkeypatch.setenv("FIRSTRADE_GCS_STATE_BUCKET", "runtime-state")
    monkeypatch.setenv("FIRSTRADE_STATE_PREFIX", "firstrade-platform")

    globs = heartbeat._report_globs(
        dt.datetime(2026, 5, 31, tzinfo=dt.timezone.utc),
        dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),
    )

    assert globs == [
        "gs://runtime-state/firstrade-platform/strategy-runs/**/2026-05/*.json",
        "gs://runtime-state/firstrade-platform/strategy-runs/**/2026_05/*.json",
        "gs://runtime-state/firstrade-platform/strategy-runs/**/2026-06/*.json",
        "gs://runtime-state/firstrade-platform/strategy-runs/**/2026_06/*.json",
    ]
