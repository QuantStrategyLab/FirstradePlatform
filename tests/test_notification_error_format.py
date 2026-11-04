"""Offline P10 error format regressions; synthetic inputs only."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from notifications import unified
from test_notification_consolidation import ENV, Store, candidate, source, NOW


@pytest.mark.parametrize("flag", ["true", "false"])
@pytest.mark.parametrize("changes", [
    {"ok": False, "error": "HTTP 503"},
    {"execution_blocked": True},
    {"orders_pending_count": 1},
    {"report_persistence_error": "missing"},
])
def test_cycle_errors_use_fixed_template_without_healthy_claims(flag, changes):
    from application.rebalance_service import _publish_cycle_notification
    sent = []
    report, result = source(**changes)
    result.update(account="fake-private-account", strategy_display_name="fake-private-alias")
    settings = SimpleNamespace(notify_lang="zh")
    outcome = unified.notify_cycle(report, result, settings=settings,
        send=lambda: _publish_cycle_notification(result, settings=settings,
            notification_sender=lambda text: sent.append(text) or True, log_message=lambda *a, **kw: None),
        env={**ENV, unified.FLAG: flag}, now=NOW, store=Store())
    assert outcome["transport_acknowledged"] is True
    assert len(sent) == 1
    assert sent[0].startswith("【资产管理｜运行错误】")
    assert "是否提交订单：未知" in sent[0]
    assert not any(value in sent[0] for value in ("无需调仓", "心跳", "fake-private", "503"))


@pytest.mark.parametrize("locale", ["zh", "en"])
def test_error_template_preserves_unknown_time_and_evidence(locale):
    text = unified.alert_text("fake-private-kind", locale)
    assert text.startswith("【资产管理｜运行错误】" if locale == "zh" else "[Asset management | Runtime error]")
    assert "fake-private-kind" not in text
    assert ("时间：未核实" if locale == "zh" else "Time: unverified") in text
    assert ("运行链接不可用" if locale == "zh" else "run link unavailable") in text


def test_heartbeat_flag_off_uses_same_error_format(monkeypatch):
    from scripts import execution_report_heartbeat as heartbeat
    monkeypatch.setenv(unified.FLAG, "false")
    monkeypatch.setenv("QSL_NOTIFY_LANG", "zh")
    sent = []
    monkeypatch.setattr(heartbeat, "_send_telegram", lambda text: sent.append(text) or True)
    assert heartbeat._deliver_alert("fake-private-alias HTTP 503", kind="receipt_missing", now=NOW)
    assert sent[0].startswith("【资产管理｜运行错误】")
    assert "fake-private" not in sent[0]


@pytest.mark.parametrize("flag", ["true", "false"])
def test_healthy_cycle_writes_candidate_without_legacy_body(flag):
    report, result = source()
    store = Store()
    def forbidden():
        pytest.fail("healthy result must not send an immediate body")
    outcome = unified.notify_cycle(report, result, settings=None, send=forbidden,
        env={**ENV, unified.FLAG: flag}, now=NOW, store=store)
    assert outcome["candidate_persisted"] is True
    assert candidate(store)["runs"][0]["cycle_count"] == 1


@pytest.mark.parametrize("url", [
    "https://example.test/private-account/actions/runs/1",
    "https://github.com/QuantStrategyLab/FirstradePlatform/actions/runs/1?token=fake-secret",
    "https://user:fake-secret@github.com/QuantStrategyLab/FirstradePlatform/actions/runs/1",
])
def test_private_or_untrusted_evidence_url_is_not_echoed(url):
    text = unified.alert_text("real_error", "en", run_url=url)
    assert url not in text and "fake-secret" not in text
    assert "run link unavailable" in text


def test_authentic_workflow_link_and_observed_time_are_preserved():
    url = "https://github.com/QuantStrategyLab/FirstradePlatform/actions/runs/1"
    text = unified.alert_text("runtime_guard", "en", occurred_at=NOW,
                              source="runtime_guard", run_url=url)
    assert url in text and "2026-10-17 08:00:00 SGT" in text
    assert "runtime monitor" in text
