"""Exercise real HTTP error handling and Telegram formatting with offline I/O."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
import requests

import main
from application.firstrade_client import FirstradeBrokerClient, FirstradeCredentials
from notifications import unified


class MemoryStore:
    def __init__(self):
        self.data = {}

    def read(self, key):
        return deepcopy(self.data.get(key, (None, None)))

    def compare_and_set(self, key, version, value):
        current = self.data.get(key, (None, None))[0]
        if current != version:
            return False
        self.data[key] = ((current or 0) + 1, deepcopy(value))
        return True


@pytest.mark.parametrize("lang", ["en", "zh"])
@pytest.mark.parametrize("unified_enabled", [False, True])
def test_account_selection_failure_through_real_alert_sender(
    monkeypatch, capsys, lang, unified_enabled,
):
    # Synthetic data only. Keep select_account, HTTP error handling, notification
    # routing and build_sender real; replace broker/storage/network boundaries.
    account = "87654321"
    client = FirstradeBrokerClient(FirstradeCredentials(username="synthetic", password="synthetic"))
    monkeypatch.setattr(client, "account_numbers", lambda: [])
    monkeypatch.setattr(main, "run_session_check", lambda: client.select_account(account))
    target = dict(platform_id="firstrade", strategy_profile="synthetic",
                  service_name="synthetic-service", account_scope="synthetic-scope",
                  account_selector=[account], deployment_selector="synthetic-slot", dry_run_only=False)
    env = {
        "FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP": "true",
        unified.FLAG: str(unified_enabled).lower(),
        "QSL_ROUTINE_NOTIFICATION_MODE": "daily_digest",
        "RUNTIME_TARGET_ENABLED": "true", "RUNTIME_TARGET_JSON": json.dumps(target),
        "DIGEST_STORE_TARGET": "synthetic", "DIGEST_OPAQUE_ACCOUNT_UID": "opaque-synthetic",
        "DIGEST_TARGET_ID": "target-synthetic", "DIGEST_CANDIDATES_GCS_URI": "gs://synthetic/candidates",
        # No archive source: the immediate incident path must still work.
        "EXECUTION_REPORT_GCS_URI": "", "DIGEST_TARGET_BINDINGS_JSON": "",
        "QSL_NOTIFICATION_MARKER_GCS_URI": "", "DIGEST_NOTIFICATION_SCHEDULE_JSON": "",
        "K_SERVICE": "synthetic-service", "ACCOUNT_REGION": "synthetic-region",
        "STRATEGY_DISPLAY_NAME": "synthetic", "QSL_NOTIFY_LANG": lang,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(main, "_get_telegram_token", lambda: "synthetic-token")
    monkeypatch.delenv("QSL_GLOBAL_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("GLOBAL_TELEGRAM_CHAT_ID", "synthetic-chat")
    store = MemoryStore()
    monkeypatch.setattr(unified, "GcsJsonStore", lambda: store)
    sent = []

    def post(url, *, json, timeout):
        assert url == "https://api.telegram.org/botsynthetic-token/sendMessage"
        assert timeout == 15
        sent.append(json)
        return SimpleNamespace(status_code=200, json=lambda: {"ok": True})

    monkeypatch.setattr(requests, "post", post)
    response = main.app.test_client().post("/probe")
    assert response.status_code == 500
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["runtime_error_notification_attempted"] is True
    assert "******21" in payload["error"]
    assert len(sent) == 1
    assert sent[0]["chat_id"] == "synthetic-chat"
    assert ("health check failed" if lang == "en" else "健康检查失败") in sent[0]["text"]
    assert "Requested account" not in sent[0]["text"]
    surfaces = [response.get_data(as_text=True), json.dumps(sent), capsys.readouterr().out]
    for surface in surfaces:
        assert account not in surface
        assert account[-4:] not in surface
    if unified_enabled:
        assert any("/_notification_markers/" in key for key in store.data)
        assert any("/_notification_events/" in key for key in store.data)


@pytest.mark.parametrize("account", ["87654321", "****4321"])
@pytest.mark.parametrize("lang", ["en", "zh"])
def test_cycle_alert_redacts_raw_and_legacy_accounts_at_log_and_transport(monkeypatch, account, lang):
    from application.rebalance_service import _publish_cycle_notification

    sent, logged = [], []

    def post(url, *, json, timeout):
        assert url == "https://api.telegram.org/botsynthetic-token/sendMessage"
        sent.append(json)
        return SimpleNamespace(status_code=200, json=lambda: {"ok": True})

    monkeypatch.setattr(requests, "post", post)
    result = {"account": account, "strategy_profile": "synthetic", "portfolio": {},
              "execution": {}, "submitted_orders": [], "skipped_orders": [],
              "execution_blocked": True, "execution_blocking_skips": [{"reason": "broker_rejected"}]}
    settings = SimpleNamespace(tg_token="synthetic-token", tg_chat_id="synthetic-chat", notify_lang=lang)
    assert _publish_cycle_notification(result, settings=settings, log_message=logged.append) is True
    assert len(sent) == 1
    assert logged and "******21" in "\n".join(logged)
    for surface in (json.dumps(sent), "\n".join(logged)):
        assert "87654321" not in surface
        assert "4321" not in surface
    assert result["account"] == account  # Broker-facing input remains intact.
