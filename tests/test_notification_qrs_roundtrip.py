"""Real Firstrade producer -> pinned QRS intake/merge/aggregate/render, offline."""
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from notifications import unified
from test_notification_consolidation import ENV, NOW, Store, archive_read, candidate, notify, source

QRS_SHA = "8f5eee7e72577682e6e897ceb5f02014e49a40a7"
SNAPSHOT = Path(__file__).parent / "fixtures/qrs_notification_716"
EXPECTED = [{"platform_id": "firstrade", "opaque_account_uid": "opaque-synthetic",
             "target_id": "target-synthetic", "strategy_profile": "synthetic"}]


@pytest.fixture
def qrs(monkeypatch):
    manifest = json.loads((SNAPSHOT / "provenance.json").read_text())
    assert manifest["source_sha"] == QRS_SHA
    for name, metadata in manifest["files"].items():
        assert hashlib.sha256((SNAPSHOT / name).read_bytes()).hexdigest() == metadata["vendored_sha256"]
    names = {name.split("/")[0].removesuffix(".py") for name in manifest["files"]}
    names.update({"notification_snapshot.unified_contract", "notification_snapshot.digest_routing"})
    saved = {name: sys.modules[name] for name in names if name in sys.modules}
    for name in saved:
        del sys.modules[name]
    monkeypatch.syspath_prepend(str(SNAPSHOT))
    monkeypatch.setenv("QRS_UNIFIED_NOTIFICATION_ENABLED", "true")
    monkeypatch.setenv("DIGEST_EXPECTED_TARGETS_JSON", json.dumps(EXPECTED))
    # Display defaults are unrelated to the wire contract; use synthetic labels.
    display = importlib.import_module("display_names")
    monkeypatch.setattr(display, "_defaults", lambda: {})
    try:
        yield SimpleNamespace(
            intake=importlib.import_module("merge_digest_candidates"),
            adapter=importlib.import_module("notification_candidate_adapter"),
            aggregator=importlib.import_module("daily_digest_aggregator"),
        )
    finally:
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def roundtrip(qrs, payload, tmp_path, *, observations=1):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    for index in range(observations):
        (inputs / f"producer-{index}.json").write_text(json.dumps(payload))
    # Exercise QRS's actual file intake/export; no hand-written run row.
    merged, stats = qrs.intake.merge(None, inputs)
    assert stats["file_rows"] == len(payload["runs"]) * observations
    rows = qrs.intake._rows(merged)
    current, stale = qrs.adapter.partition(rows, "2026-10-16")
    assert stale == []
    qrs.adapter.validate_expected(EXPECTED)
    received, summary = qrs.adapter.aggregate(current, EXPECTED)
    output = tmp_path / "merged.json"
    output.write_text(json.dumps(merged))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"notifications": {"quant_sentinel": {"daily_digest": {
        "aggregator": {"expected_targets": EXPECTED}}}}}))
    digest, text, collected = qrs.aggregator.build_digest_payload(
        business_day="2026-10-16", locale="zh", candidates_path=output,
        config_path=config, include_github=False)
    receipt = qrs.aggregator.build_receipt(digest, collected, message_chars=len(text))
    assert collected.notification_summary == summary
    assert receipt["notification_summary"] == summary
    assert collected.coverage.is_complete_for_verified_idle
    assert receipt["failures"] == []
    return received, summary, receipt, text


@pytest.mark.parametrize("producer", ["runtime", "archive", "heartbeat"])
def test_qrs_roundtrip_two_healthy_runs_have_known_coverage_and_two_events(qrs, tmp_path, producer):
    store, sent = Store(), []
    reports = []
    for run_id in ("business-run-a", "business-run-b"):
        report, result = source(run_id)
        reports.append(report)
        if producer == "runtime":
            assert notify(store, report, result, sent)["route"] == "digest_candidate"
    if producer == "archive":
        unified.emit_candidates(ENV, now=NOW, store=store, read=archive_read(reports))
    if producer == "heartbeat":
        outcome = unified.notify_heartbeat(kind="healthy_heartbeat", now=NOW,
            env={**ENV, "GITHUB_RUN_ID": "monitor-is-not-a-business-run"}, store=store,
            read=archive_read(reports), recovery_reports=reports,
            send=lambda: sent.append("unexpected") or True)
        assert outcome["candidate_persisted"] is True
    rows, summary, receipt, text = roundtrip(qrs, candidate(store), tmp_path)
    assert {row["source_run_id"] for row in rows} == {"business-run-a", "business-run-b"}
    assert summary["source_inventory"]["firstrade"] == {"expected_targets": 1, "covered_targets": 1, "runs": 2}
    assert summary["counts_unknown"] is False and summary["run_count_unknown"] is False
    assert summary["event_count"] == 2 and summary["immediate_events"] == 0
    assert receipt["run_count"] == 2 and receipt["total_fills"] == 0
    assert "订单：0" in text and "全窗口统计未知" not in text
    assert sent == []


def test_qrs_roundtrip_zero_order_run_is_known_zero(qrs, tmp_path):
    store, sent = Store(), []
    report, result = source()
    notify(store, report, result, sent)
    rows, summary, receipt, text = roundtrip(qrs, candidate(store), tmp_path)
    assert rows[0]["order_count"] == rows[0]["fill_count"] == 0
    assert summary["counts_unknown"] is False and receipt["run_count"] == 1
    # Firstrade's healthy zero-run rendering uses the aggregate zero label.
    assert "订单：0" in text and "成交：0" in text and "未知" not in text


def test_qrs_roundtrip_real_order_is_immediate_and_counted_once(qrs, tmp_path):
    store, sent = Store(), []
    report, result = source(status="completed", orders=[{"status": "filled",
        "broker_order_id": "synthetic-order", "fills": [{"execution_id": "synthetic-fill"}]}])
    notify(store, report, result, sent)
    rows, summary, receipt, text = roundtrip(qrs, candidate(store), tmp_path)
    assert len(sent) == 1 and rows[0]["order_count"] == rows[0]["fill_count"] == 1
    assert summary["event_count"] == summary["immediate_events"] == 1
    assert summary["counts_unknown"] is False
    assert receipt["run_count"] == 1 and receipt["total_fills"] == 1
    assert "订单：1" in text and "成交：1" in text


def test_qrs_roundtrip_duplicate_runtime_archive_and_intake_observations_do_not_recount(qrs, tmp_path):
    store, sent = Store(), []
    report, result = source()
    for _ in range(2):
        notify(store, report, result, sent)
    unified.emit_candidates(ENV, now=NOW, store=store, read=archive_read([deepcopy(report)]))
    rows, summary, receipt, _ = roundtrip(qrs, candidate(store), tmp_path, observations=2)
    assert len(rows) == 1 and rows[0]["cycle_count"] == 1
    assert rows[0]["order_count"] == rows[0]["fill_count"] == 0
    assert summary["source_inventory"]["firstrade"]["runs"] == 1
    assert summary["event_count"] == 1 and summary["count_conflicts"] == 0
    assert summary["counts_unknown"] is False and receipt["run_count"] == 1
    assert sent == []
