"""Call recorder goldens captured on main c320fa004c64116996b6199dbd063892c0286fb1."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import main
from application.rebalance_service import _publish_cycle_notification
from notifications.telegram import render_cycle_notification
from scripts import execution_report_heartbeat as heartbeat

GOLDEN = Path(__file__).parent / "fixtures" / "notification_main_c320fa0.json"
NOW = datetime(2026, 10, 16, 23, tzinfo=timezone.utc)


def record_main(monkeypatch, *, outcome, lang, notify=True):
    calls = []
    result = {"ok": outcome != "error", "execution_status": outcome,
              "strategy_run_stage": "COMPLETED", "submitted_orders": [],
              "strategy_profile": "synthetic", "strategy_display_name": "Example",
              "dry_run_only": False}
    if outcome == "filled":
        result["submitted_orders"] = [{"symbol": "AAA", "quantity": 1, "status": "filled"}]
    if outcome == "error":
        result["error"] = "synthetic_failure"
    settings = SimpleNamespace(dry_run_only=False, notify_lang=lang, tg_token=None, tg_chat_id=None)
    monkeypatch.setattr(main, "_runtime_settings", lambda **kw: settings)
    monkeypatch.setattr(main, "_build_runtime_report", lambda *a, **kw: {"run_id": "synthetic-run"})
    monkeypatch.setattr(main, "attach_strategy_result_execution_receipt",
                        lambda *a, **kw: calls.append(["attach", kw]))
    monkeypatch.setattr(main, "finalize_runtime_report",
                        lambda *a, **kw: calls.append(["finalize", kw]))
    monkeypatch.setattr(main, "_persist_runtime_report",
                        lambda report: calls.append(["persist", deepcopy(report)]))

    def cycle(**kw):
        calls.append(["cycle", {k: v for k, v in kw.items() if k != "runtime_settings"}])
        if kw["send_cycle_notification"]:
            result["notification_sent"] = _publish_cycle_notification(result, settings=settings,
                notification_sender=lambda text: calls.append(["send", text]) or True,
                log_message=lambda text: calls.append(["log", text]))
        return result

    monkeypatch.setattr(main, "run_strategy_cycle", cycle)
    returned = main._run_strategy_cycle_with_report(send_cycle_notification=notify)
    return {"calls": calls, "result": returned,
            "renderer": asdict(render_cycle_notification(result, lang=lang))}


def record_heartbeat(monkeypatch, *, outcome, notify):
    calls = []
    monkeypatch.setenv("RUNTIME_HEARTBEAT_NAME", "Example")
    monkeypatch.setenv("RUNTIME_HEARTBEAT_NOTIFY_ON_SUCCESS", str(notify).lower())
    monkeypatch.setattr(heartbeat, "_runtime_target_enabled", lambda: True)
    monkeypatch.setattr(heartbeat, "runtime_target_configuration_present", lambda env: False)
    monkeypatch.setattr(heartbeat, "load_runtime_targets", lambda env: [])
    monkeypatch.setattr(heartbeat, "_hydrate_runtime_target_schedules", lambda targets, **kw: targets)
    monkeypatch.setattr(heartbeat, "filter_due_targets", lambda *a, **kw: ([], False))
    monkeypatch.setattr(heartbeat, "_heartbeat_skip_reason_for_schedule", lambda *a: None)
    monkeypatch.setattr(heartbeat, "_load_required_services", lambda: [])
    monkeypatch.setattr(heartbeat, "_report_globs", lambda *a: ["gs://synthetic/reports"])

    def listing(uri, **kw):
        calls.append(["list", uri, kw])
        return [] if outcome == "missing" else [{"url": "gs://synthetic/reports/source.json",
            "metadata": {"updated": NOW.isoformat()}}]

    monkeypatch.setattr(heartbeat, "_list_gcs_objects", listing)
    def read(uri, **kw):
        calls.append(["read", uri, kw])
        return {"status": "ok", "summary": {"execution_status": outcome, "order_events_count": 0},
                "service_name": "synthetic-service", "dry_run": False, "errors": [], "diagnostics": {},
                "started_at": NOW.isoformat(), "finished_at": NOW.isoformat(),
                "market": "US", "market_calendar": "NYSE", "market_timezone": "America/New_York"}
    monkeypatch.setattr(heartbeat, "_cat_gcs_json", read)
    monkeypatch.setattr(heartbeat, "_send_telegram", lambda text: calls.append(["send", text]) or True)
    return {"exit_code": heartbeat.main(now=NOW), "calls": calls}


def record_all(monkeypatch):
    records = {}
    for lang in ("zh", "en"):
        monkeypatch.setenv("NOTIFY_LANG", lang)
        for outcome in ("no_action", "filled", "error"):
            with monkeypatch.context() as local:
                records[f"main:{lang}:{outcome}"] = record_main(local, outcome=outcome, lang=lang)
        for outcome in ("no_action", "blocked", "missing"):
            for notify in (False, True):
                with monkeypatch.context() as local:
                    records[f"heartbeat:{lang}:{outcome}:{notify}"] = record_heartbeat(
                        local, outcome=outcome, notify=notify)
        with monkeypatch.context() as local:
            local.setattr(main, "request", SimpleNamespace(path="/run"))
            local.setenv("STRATEGY_DISPLAY_NAME", "Example")
            local.setenv("ACCOUNT_REGION", "US")
            local.setattr(main, "_telegram_notification_targets", lambda: (("synthetic", "synthetic"),))
            calls = []
            local.setattr(main, "build_sender", lambda *a: lambda text: calls.append(text) or True)
            records[f"runtime_error:{lang}"] = {"result": main._notify_runtime_error(RuntimeError("synthetic")), "sends": calls}
    with monkeypatch.context() as local:
        records["broker_cycle"] = record_broker_cycle(local)
    return records


def record_broker_cycle(monkeypatch):
    import application.rebalance_service as rebalance
    from application.firstrade_client import FirstradeCredentials
    from test_rebalance_service import (FakeFirstradeClient, FakeStateStore, FakeStrategyRuntime,
                                       _runtime_settings_with_persistence)
    calls = []
    client = FakeFirstradeClient(None, live_trading_enabled=True)
    client.accounts = ["synthetic-selector"]
    store = FakeStateStore()
    for obj, prefix, methods in (
        (client, "broker", ("connect", "account_numbers", "select_account", "get_balances", "get_positions",
                            "get_quote", "get_ohlc", "get_order_status", "place_stock_order")),
        (store, "state", ("read_json", "write_json", "create_json")),
    ):
        for name in methods:
            original = getattr(obj, name)
            def capture(*args, _name=name, _original=original, _prefix=prefix, **kwargs):
                calls.append([_prefix, _name, json.loads(json.dumps([args, kwargs], default=str))])
                return _original(*args, **kwargs)
            monkeypatch.setattr(obj, name, capture)
    settings = _runtime_settings_with_persistence(dry_run_only=False, live_trading_enabled=True,
                                                  live_order_ack=True, persist_strategy_runs=True)
    monkeypatch.setattr(rebalance, "load_strategy_runtime", lambda *a, **kw: FakeStrategyRuntime())
    monkeypatch.setattr(rebalance, "_utcnow", lambda: NOW)
    monkeypatch.setattr(main, "_runtime_settings", lambda **kw: settings)
    monkeypatch.setattr(main, "_build_runtime_report", lambda *a, **kw: {"run_id": "synthetic-run"})
    monkeypatch.setattr(main, "attach_strategy_result_execution_receipt", lambda *a, **kw: calls.append(["receipt"]))
    monkeypatch.setattr(main, "finalize_runtime_report", lambda *a, **kw: calls.append(["finalize"]))
    monkeypatch.setattr(main, "_persist_runtime_report", lambda report: calls.append(["report_persist"]))
    def cycle(**kwargs):
        return rebalance.run_strategy_cycle(**kwargs, credentials=FirstradeCredentials(username="", password=""),
            client_factory=lambda *a, **kw: client, state_store=store,
            env_reader=lambda name, default=None: default,
            notification_sender=lambda text: calls.append(["send", text]) or True)
    monkeypatch.setattr(main, "run_strategy_cycle", cycle)
    result = main._run_strategy_cycle_with_report(dispatch_plugin_alerts=False)
    return {"calls": calls, "outcome": {k: result.get(k) for k in (
        "ok", "execution_status", "orders_pending_count", "submitted_orders", "notification_sent")}}


@pytest.mark.parametrize("flag", [None, "", "false", "invalid", "1"])
@pytest.mark.parametrize("mode", ["immediate", "daily_digest"])
def test_flags_off_entrypoints_renderers_match_main_golden(monkeypatch, flag, mode):
    for name in ("QSL_ROUTINE_NOTIFICATION_MODE", "QSL_NOTIFY_LANG", "GCP_PROJECT_ID",
                 "GOOGLE_CLOUD_PROJECT", "RUNTIME_HEARTBEAT_GCP_PROJECT_ID",
                 "GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"):
        monkeypatch.delenv(name, raising=False)
    if flag is None:
        monkeypatch.delenv("FIRSTRADE_UNIFIED_NOTIFICATION_ENABLED", raising=False)
    else:
        monkeypatch.setenv("FIRSTRADE_UNIFIED_NOTIFICATION_ENABLED", flag)
    monkeypatch.setenv("QSL_ROUTINE_NOTIFICATION_MODE", mode)
    actual = record_all(monkeypatch)
    expected = json.loads(GOLDEN.read_text())[mode]
    for lang in ("zh", "en"):
        for outcome in ("no_action", "filled", "error"):
            key = f"main:{lang}:{outcome}"
            assert actual[key]["renderer"] == expected[key]["renderer"]
            assert actual[key]["result"]["ok"] == expected[key]["result"]["ok"]
            assert actual[key]["result"]["execution_status"] == expected[key]["result"]["execution_status"]
        assert actual[f"runtime_error:{lang}"] == expected[f"runtime_error:{lang}"]
        for outcome in ("blocked", "missing"):
            for notify in (False, True):
                key = f"heartbeat:{lang}:{outcome}:{notify}"
                # The pinned QPK privacy contract replaces raw technical details.
                # Keep the historical golden immutable and compare every control call.
                if outcome == "blocked":
                    raw = ("技术详情（原文）：\nsynthetic-service: rejected inner execution status"
                           if lang == "zh" else
                           "Technical details (original):\nsynthetic-service: rejected inner execution status")
                    safe = ("排查详情请查看工作流或云端运行日志。" if lang == "zh" else
                            "See the workflow or cloud runtime logs for diagnostic details.")
                    send_call = expected[key]["calls"][-1]
                    assert send_call[0] == "send" and send_call[1].endswith(raw)
                    send_call[1] = send_call[1][:-len(raw)] + safe
                assert actual[key] == expected[key]
    actual_broker = actual["broker_cycle"]
    expected_broker = expected["broker_cycle"]
    # The historical golden retains the old display mask. Only persisted account
    # payloads change; storage locators and every broker call must stay exact.
    for call in expected_broker["calls"]:
        if call[0] == "state" and call[1] in {"write_json", "create_json"}:
            payload = call[2][0][1]
            if "account" in payload:
                assert payload["account"] == "**************ctor"
                payload["account"] = "****************or"
    assert [call for call in actual_broker["calls"] if call[0] in {"broker", "state"}] == [
        call for call in expected_broker["calls"] if call[0] in {"broker", "state"}]
    for key in ("ok", "execution_status", "orders_pending_count", "submitted_orders"):
        assert actual_broker["outcome"][key] == expected_broker["outcome"][key]
    for lang in ("zh", "en"):
        for notify in (False, True):
            healthy = actual[f"heartbeat:{lang}:no_action:{notify}"]
            assert healthy["exit_code"] == 1  # Fixture has no source-bound durable candidate.
            assert len([call for call in healthy["calls"] if call[0] == "send"]) == 1

