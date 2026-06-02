from __future__ import annotations

import pytest

pytest.importorskip("flask")

import main


def test_run_endpoint_is_disabled_without_explicit_http_gate(monkeypatch):
    monkeypatch.delenv("FIRSTRADE_RUN_STRATEGY_ON_HTTP", raising=False)
    client = main.app.test_client()

    response = client.get("/run")

    assert response.status_code == 403
    assert response.get_json()["ok"] is False


def test_run_endpoint_calls_strategy_cycle_when_gate_enabled(monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RUN_STRATEGY_ON_HTTP", "true")
    monkeypatch.setattr(main, "run_strategy_cycle", lambda: {"ok": True, "action_done": False})
    client = main.app.test_client()

    response = client.post("/run")

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "action_done": False}


def test_session_check_endpoint_is_disabled_without_explicit_http_gate(monkeypatch):
    monkeypatch.delenv("FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP", raising=False)
    client = main.app.test_client()

    response = client.get("/session-check")

    assert response.status_code == 403
    assert response.get_json()["ok"] is False


def test_session_check_endpoint_calls_service_when_gate_enabled(monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP", "true")
    monkeypatch.setattr(
        main,
        "run_session_check",
        lambda: {"ok": True, "session_reused": True, "snapshot_persisted": True},
    )
    client = main.app.test_client()

    response = client.post("/session-check")

    assert response.status_code == 200
    assert response.get_json() == {
        "ok": True,
        "session_reused": True,
        "snapshot_persisted": True,
    }


def test_root_post_calls_strategy_cycle_when_gate_enabled(monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RUN_STRATEGY_ON_HTTP", "true")
    monkeypatch.setattr(main, "run_strategy_cycle", lambda: {"ok": True, "action_done": False})
    client = main.app.test_client()

    response = client.post("/")

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "action_done": False}


def test_run_endpoint_notifies_telegram_on_strategy_cycle_error(monkeypatch):
    sent_messages = []

    def fake_build_sender(token, chat_id):
        def send(message):
            sent_messages.append((token, chat_id, message))

        return send

    monkeypatch.setenv("FIRSTRADE_RUN_STRATEGY_ON_HTTP", "true")
    monkeypatch.setenv("TELEGRAM_TOKEN", "token-1")
    monkeypatch.setenv("GLOBAL_TELEGRAM_CHAT_ID", "chat-1")
    monkeypatch.setenv("STRATEGY_PROFILE", "mega_cap_leader_rotation_top50_balanced")
    monkeypatch.setattr(main, "build_sender", fake_build_sender)
    monkeypatch.setattr(
        main,
        "run_strategy_cycle",
        lambda: (_ for _ in ()).throw(ValueError("snapshot denied")),
    )
    client = main.app.test_client()

    response = client.post("/run")

    assert response.status_code == 500
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["error"] == "snapshot denied"
    assert payload["runtime_error_notification_attempted"] is True
    assert len(sent_messages) == 1
    assert sent_messages[0][0] == "token-1"
    assert sent_messages[0][1] == "chat-1"
    assert "Firstrade strategy run failed" in sent_messages[0][2]
    assert "ValueError: snapshot denied" in sent_messages[0][2]
    assert "strategy: mega_cap_leader_rotation_top50_balanced" in sent_messages[0][2]


def test_run_endpoint_error_does_not_require_telegram_config(monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RUN_STRATEGY_ON_HTTP", "true")
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("GLOBAL_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("CRISIS_ALERT_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("CRISIS_ALERT_TELEGRAM_CHAT_IDS", raising=False)
    monkeypatch.setattr(
        main,
        "run_strategy_cycle",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    client = main.app.test_client()

    response = client.post("/run")

    assert response.status_code == 500
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["error"] == "RuntimeError: boom"
    assert payload["runtime_error_notification_attempted"] is False


def test_scheduler_health_routes_accept_post():
    client = main.app.test_client()

    precheck_response = client.post("/precheck")
    probe_response = client.post("/probe")

    assert precheck_response.status_code == 200
    assert probe_response.status_code == 200
