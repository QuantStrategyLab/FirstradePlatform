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


def test_scheduler_health_routes_accept_post():
    client = main.app.test_client()

    precheck_response = client.post("/precheck")
    probe_response = client.post("/probe")

    assert precheck_response.status_code == 200
    assert probe_response.status_code == 200
