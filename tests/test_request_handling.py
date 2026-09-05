from __future__ import annotations

import pytest

pytest.importorskip("flask")

import main


@pytest.fixture(autouse=True)
def _assume_market_open_for_http_tests(monkeypatch, request):
    if request.node.name == "test_run_endpoint_skips_when_market_closed":
        return
    monkeypatch.setattr(main, "_should_skip_for_market_hours", lambda: (False, None))


def route_methods():
    methods_by_route = {}
    for rule in main.app.url_map.iter_rules():
        methods_by_route.setdefault(rule.rule, set()).update(rule.methods - {"HEAD", "OPTIONS"})
    return {route: sorted(methods) for route, methods in methods_by_route.items()}


def test_cloud_run_route_contracts_are_registered():
    assert route_methods() == {
        "/": ["GET"],
        "/health": ["GET"],
        "/healthz": ["GET"],
        "/profiles": ["GET"],
        "/smoke": ["GET"],
        "/run": ["POST"],
        "/dry-run": ["GET", "POST"],
        "/paper-command-consumer": ["POST"],
        "/reconcile": ["POST"],
        "/monitor-dispatch": ["GET", "POST"],
        "/probe": ["POST"],
        "/static/<path:filename>": ["GET"],
    }


def test_health_route_returns_service_contract(monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RUN_SMOKE_ON_HTTP", "true")
    monkeypatch.delenv("FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP", raising=False)
    monkeypatch.delenv("FIRSTRADE_RUN_STRATEGY_ON_HTTP", raising=False)
    client = main.app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["service"] == "firstrade-platform"
    assert payload["api_kind"] == "unofficial-reverse-engineered"
    assert payload["strategy_domain"] == "us_equity"
    assert payload["smoke_on_http"] is True
    assert payload["session_check_on_http"] is False
    assert payload["strategy_run_on_http"] is False
    assert "as_of" in payload


@pytest.mark.parametrize("strategy_gate", [None, "false", "true"])
@pytest.mark.parametrize("path", ["/run", "/probe"])
def test_execution_routes_reject_get_without_runtime_calls(monkeypatch, strategy_gate, path):
    if strategy_gate is None:
        monkeypatch.delenv("FIRSTRADE_RUN_STRATEGY_ON_HTTP", raising=False)
    else:
        monkeypatch.setenv("FIRSTRADE_RUN_STRATEGY_ON_HTTP", strategy_gate)
    monkeypatch.setenv("FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP", "true")

    def fail_runtime_call(*_args, **_kwargs):
        pytest.fail("GET must not reach strategy, session, or broker runtime code")

    monkeypatch.setattr(main, "_runtime_target_enabled_env", fail_runtime_call)
    monkeypatch.setattr(main, "_run_strategy_cycle_with_report", fail_runtime_call)
    monkeypatch.setattr(main, "run_session_check", fail_runtime_call)
    monkeypatch.setattr(main, "FirstradeBrokerClient", fail_runtime_call)
    client = main.app.test_client()

    response = client.get(path)

    assert response.status_code == 405
    assert "POST" in response.headers["Allow"]


@pytest.mark.parametrize(
    ("path", "gate"),
    [
        ("/run", "FIRSTRADE_RUN_STRATEGY_ON_HTTP"),
        ("/probe", "FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP"),
    ],
)
def test_execution_posts_still_require_explicit_http_gate(monkeypatch, path, gate):
    monkeypatch.delenv(gate, raising=False)

    def fail_runtime_call(*_args, **_kwargs):
        pytest.fail("disabled POST must not reach strategy, session, or broker runtime code")

    monkeypatch.setattr(main, "_run_strategy_cycle_with_report", fail_runtime_call)
    monkeypatch.setattr(main, "run_session_check", fail_runtime_call)
    monkeypatch.setattr(main, "FirstradeBrokerClient", fail_runtime_call)

    response = main.app.test_client().post(path)

    assert response.status_code == 403
    assert response.get_json()["ok"] is False


def test_health_endpoint_remains_available_via_get():
    response = main.app.test_client().get("/health")

    assert response.status_code == 200


def test_reconcile_disabled_before_runtime_or_client_context(monkeypatch):
    monkeypatch.delenv("FIRSTRADE_BROKER_RECONCILIATION_ENABLED", raising=False)

    def fail(*_args, **_kwargs):
        pytest.fail("disabled reconciliation must not build runtime or broker context")

    monkeypatch.setattr(main, "_runtime_settings", fail)
    monkeypatch.setattr(main, "READ_ONLY_BROKER_RECONCILIATION_CLIENT_BUILDER", fail)

    response = main.app.test_client().post("/reconcile")

    assert response.status_code == 503
    assert response.get_json() == {"status": "blocked", "reason": "broker_reconciliation_disabled"}


def test_reconcile_enabled_returns_redacted_blocked_receipt(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setenv("FIRSTRADE_BROKER_RECONCILIATION_ENABLED", "true")
    monkeypatch.setattr(
        main,
        "_runtime_settings",
        lambda: SimpleNamespace(
            runtime_target=SimpleNamespace(
                platform_id="firstrade",
                strategy_profile="sample_profile",
                account_scope="US",
                live_continuity=SimpleNamespace(
                    state="RECONCILE_ONLY",
                    baseline_id="firstrade-baseline-001",
                    baseline_target_sha256="2" * 64,
                ),
            ),
            project_id=None,
        ),
    )

    closed = []

    class FakeClient:
        def close(self):
            closed.append(True)

        def account_numbers(self):
            return ["account-sensitive-001"]

        def select_account(self, requested_account=None):
            return requested_account

        def get_balances(self, _account):
            return {"cash_balance": "100.25"}

        def get_positions(self, _account):
            return {"items": []}

        def get_orders(self, _account, *, per_page=0):
            assert per_page == 0
            return []

    monkeypatch.setenv("FIRSTRADE_ACCOUNT", "account-sensitive-001")
    monkeypatch.setattr(main, "READ_ONLY_BROKER_RECONCILIATION_CLIENT_BUILDER", FakeClient)
    monkeypatch.setattr(main, "_read_only_execution_ledger_digest", lambda **_kwargs: ("7" * 64, 0))

    response = main.app.test_client().post("/reconcile")

    assert response.status_code == 200
    payload = response.get_json()
    assert closed == [True]
    assert payload["permits_active_lkg"] is False
    assert payload["expected_digests_configured"] is False
    assert "account-sensitive-001" not in response.get_data(as_text=True)
    assert "100.25" not in response.get_data(as_text=True)


def test_run_endpoint_calls_strategy_cycle_when_gate_enabled(monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RUN_STRATEGY_ON_HTTP", "true")
    monkeypatch.setenv("RUNTIME_TARGET_ENABLED", "true")
    monkeypatch.setattr(main, "_should_skip_for_market_hours", lambda: (False, None))
    monkeypatch.setattr(main, "_run_strategy_cycle_with_report", lambda **_kwargs: {"ok": True, "action_done": False})
    client = main.app.test_client()

    response = client.post("/run")

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "action_done": False}


def test_dry_run_admission_blocks_before_strategy_cycle(monkeypatch):
    monkeypatch.setenv("QSL_PAPER_ADMISSION_ENABLED", "true")
    monkeypatch.setattr(
        main,
        "_evaluate_paper_dry_run_admission",
        lambda: {
            "status": "blocked",
            "audit_color": "red",
            "integrity_findings": ["paper_execution_command_missing"],
        },
    )
    monkeypatch.setattr(
        main,
        "_run_strategy_cycle_with_report",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("strategy cycle must not run")),
    )
    client = main.app.test_client()

    response = client.post("/dry-run")

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["action_done"] is False
    assert payload["submitted_orders"] == []
    assert payload["paper_execution_admission"]["audit_color"] == "red"


def test_run_endpoint_skips_when_market_closed(monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RUN_STRATEGY_ON_HTTP", "true")
    monkeypatch.setenv("RUNTIME_TARGET_ENABLED", "true")
    monkeypatch.setattr(
        main,
        "_should_skip_for_market_hours",
        lambda: (
            True,
            {
                "ok": True,
                "status": "skipped",
                "skip_reason": "market_closed",
                "action_done": False,
                "submitted_orders": [],
                "skipped_orders": [{"reason": "market_closed"}],
            },
        ),
    )
    monkeypatch.setattr(
        main,
        "_run_strategy_cycle_with_report",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("strategy cycle should not run")),
    )
    client = main.app.test_client()

    response = client.post("/run")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["skip_reason"] == "market_closed"
    assert payload["submitted_orders"] == []


def test_run_endpoint_returns_500_for_retryable_funding_block(monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RUN_STRATEGY_ON_HTTP", "true")
    monkeypatch.setenv("RUNTIME_TARGET_ENABLED", "true")
    monkeypatch.setattr(main, "_should_skip_for_market_hours", lambda: (False, None))
    monkeypatch.setattr(
        main,
        "_run_strategy_cycle_with_report",
        lambda **_kwargs: {
            "ok": False,
            "execution_blocked": True,
            "execution_block_retryable": True,
            "funding_blocked": True,
            "error": "Strategy execution blocked; see execution_blocking_skips.",
        },
    )
    client = main.app.test_client()

    response = client.post("/run")

    assert response.status_code == 500
    payload = response.get_json()
    assert payload["funding_blocked"] is True
    assert payload["execution_block_retryable"] is True


def test_run_endpoint_returns_500_for_retryable_execution_block(monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RUN_STRATEGY_ON_HTTP", "true")
    monkeypatch.setenv("RUNTIME_TARGET_ENABLED", "true")
    monkeypatch.setattr(
        main,
        "_run_strategy_cycle_with_report",
        lambda **_kwargs: {
            "ok": False,
            "execution_blocked": True,
            "execution_block_retryable": True,
            "funding_blocked": False,
            "error": "Strategy execution blocked; see execution_blocking_skips.",
        },
    )
    client = main.app.test_client()

    response = client.post("/run")

    assert response.status_code == 500
    payload = response.get_json()
    assert payload["execution_blocked"] is True
    assert payload["execution_block_retryable"] is True


def test_probe_endpoint_calls_service_when_gate_enabled(monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP", "true")
    sent_messages = []
    monkeypatch.setattr(
        main,
        "run_session_check",
        lambda: {"ok": True, "session_reused": True, "snapshot_persisted": True},
    )
    monkeypatch.setattr(main, "build_sender", lambda *_args, **_kwargs: sent_messages.append)
    client = main.app.test_client()

    response = client.post("/probe")

    assert response.status_code == 200
    assert response.get_json() == {
        "ok": True,
        "session_reused": True,
        "snapshot_persisted": True,
    }
    assert sent_messages == []


def test_probe_endpoint_notifies_only_on_error(monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP", "true")
    monkeypatch.setenv("TELEGRAM_TOKEN", "token-1")
    monkeypatch.setenv("GLOBAL_TELEGRAM_CHAT_ID", "chat-1")
    monkeypatch.setattr(
        main,
        "run_session_check",
        lambda: (_ for _ in ()).throw(RuntimeError("session denied")),
    )
    sent_messages = []

    def fake_build_sender(token, chat_id):
        def send(message):
            sent_messages.append((token, chat_id, message))

        return send

    monkeypatch.setattr(main, "build_sender", fake_build_sender)
    client = main.app.test_client()

    response = client.post("/probe")

    assert response.status_code == 500
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["runtime_error_notification_attempted"] is True
    assert len(sent_messages) == 1
    assert sent_messages[0][0] == "token-1"
    assert sent_messages[0][1] == "chat-1"
    assert "Firstrade health check failed" in sent_messages[0][2]
    assert "RuntimeError: session denied" in sent_messages[0][2]


def test_run_endpoint_notifies_telegram_on_strategy_cycle_error(monkeypatch):
    sent_messages = []

    def fake_build_sender(token, chat_id):
        def send(message):
            sent_messages.append((token, chat_id, message))

        return send

    monkeypatch.setenv("FIRSTRADE_RUN_STRATEGY_ON_HTTP", "true")
    monkeypatch.setenv("RUNTIME_TARGET_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_TOKEN", "token-1")
    monkeypatch.setenv("GLOBAL_TELEGRAM_CHAT_ID", "chat-1")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN", "plugin-token")
    monkeypatch.setenv("STRATEGY_PLUGIN_ALERT_TELEGRAM_CHAT_IDS", "plugin-chat")
    monkeypatch.setenv("STRATEGY_PROFILE", "russell_top50_leader_rotation")
    monkeypatch.setattr(main, "build_sender", fake_build_sender)
    monkeypatch.setattr(
        main,
        "_run_strategy_cycle_with_report",
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
    assert "strategy: russell_top50_leader_rotation" in sent_messages[0][2]


def test_run_endpoint_error_notification_uses_chinese_copy(monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RUN_STRATEGY_ON_HTTP", "true")
    monkeypatch.setenv("RUNTIME_TARGET_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_TOKEN", "token-1")
    monkeypatch.setenv("GLOBAL_TELEGRAM_CHAT_ID", "chat-1")
    monkeypatch.setenv("NOTIFY_LANG", "zh")
    monkeypatch.setenv("STRATEGY_PROFILE", "russell_top50_leader_rotation")
    sent_messages = []

    def fake_build_sender(token, chat_id):
        def send(message):
            sent_messages.append((token, chat_id, message))

        return send

    monkeypatch.setattr(main, "build_sender", fake_build_sender)
    monkeypatch.setattr(
        main,
        "_run_strategy_cycle_with_report",
        lambda: (_ for _ in ()).throw(ValueError("snapshot denied")),
    )
    client = main.app.test_client()

    response = client.post("/run")

    assert response.status_code == 500
    text = sent_messages[0][2]
    assert "Firstrade 策略运行失败" in text
    assert "策略: russell_top50_leader_rotation" in text
    assert "错误: ValueError: snapshot denied" in text


def test_run_endpoint_redacts_sensitive_error_text(monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RUN_STRATEGY_ON_HTTP", "true")
    monkeypatch.setenv("RUNTIME_TARGET_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_TOKEN", "token-1")
    monkeypatch.setenv("GLOBAL_TELEGRAM_CHAT_ID", "chat-1")
    sent_messages = []

    def fake_build_sender(token, chat_id):
        def send(message):
            sent_messages.append((token, chat_id, message))

        return send

    sensitive_error = (
        "request failed: password=supersecret123 token=abcd1234efgh "
        "https://api.telegram.org/bot123456789:ABC/sendMessage?api_key=key987654"
    )
    monkeypatch.setattr(main, "build_sender", fake_build_sender)
    monkeypatch.setattr(
        main,
        "_run_strategy_cycle_with_report",
        lambda: (_ for _ in ()).throw(RuntimeError(sensitive_error)),
    )
    client = main.app.test_client()

    response = client.post("/run")

    assert response.status_code == 500
    payload = response.get_json()
    assert "<redacted>" in payload["error"]
    assert "<redacted>" in sent_messages[0][2]
    for raw_secret in ("supersecret123", "abcd1234efgh", "123456789:ABC", "key987654"):
        assert raw_secret not in payload["error"]
        assert raw_secret not in sent_messages[0][2]


def test_run_endpoint_error_does_not_require_telegram_config(monkeypatch):
    monkeypatch.setenv("FIRSTRADE_RUN_STRATEGY_ON_HTTP", "true")
    monkeypatch.setenv("RUNTIME_TARGET_ENABLED", "true")
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("GLOBAL_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("STRATEGY_PLUGIN_ALERT_TELEGRAM_CHAT_IDS", raising=False)
    monkeypatch.setattr(
        main,
        "_run_strategy_cycle_with_report",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    client = main.app.test_client()

    response = client.post("/run")

    assert response.status_code == 500
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["error"] == "RuntimeError: boom"
    assert payload["runtime_error_notification_attempted"] is False


def test_scheduler_routes_accept_post(monkeypatch):
    observed = {}

    def fake_run_strategy_cycle_with_report(**kwargs):
        observed.update(kwargs)
        return {"ok": True, "action_done": False}

    monkeypatch.setenv("FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP", "true")
    monkeypatch.setattr(main, "_run_strategy_cycle_with_report", fake_run_strategy_cycle_with_report)
    monkeypatch.setattr(main, "run_session_check", lambda: {"ok": True, "session_reused": True})
    client = main.app.test_client()

    dry_run_response = client.post("/dry-run")
    probe_response = client.post("/probe")

    assert dry_run_response.status_code == 200
    assert dry_run_response.get_json()["ok"] is True
    assert observed == {
        "dry_run_override": True,
        "send_cycle_notification": False,
        "dispatch_plugin_alerts": False,
    }
    assert probe_response.status_code == 200
    assert probe_response.get_json()["ok"] is True


def test_monitor_dispatch_post_dispatches_due_targets(monkeypatch):
    observed = {}

    def fake_dispatch(targets):
        observed["targets"] = targets
        return {"ok": True, "dispatches_due": 0}

    monkeypatch.setattr(main, "load_monitor_targets", lambda: [{"service_name": "firstrade-quant-service"}])
    monkeypatch.setattr(main, "dispatch_due_monitors", fake_dispatch)
    client = main.app.test_client()

    response = client.post("/monitor-dispatch")

    assert response.status_code == 200
    assert response.get_json()["dispatches_due"] == 0
    assert observed["targets"][0]["service_name"] == "firstrade-quant-service"


def test_reconciliation_default_builder_uses_cached_only_client(monkeypatch):
    from types import SimpleNamespace
    calls = []
    credentials = object()
    def from_env(**kwargs):
        assert kwargs == {"include_login_credentials": False}
        return credentials
    def client_factory(actual, *, live_trading_enabled):
        assert actual is credentials and live_trading_enabled is False
        return SimpleNamespace(connect_read_only=lambda: calls.append("cached-only") or "client")
    monkeypatch.setattr(main.FirstradeCredentials, "from_env", from_env)
    monkeypatch.setattr(main, "FirstradeBrokerClient", client_factory)
    assert main.READ_ONLY_BROKER_RECONCILIATION_CLIENT_BUILDER() == "client"
    assert calls == ["cached-only"]


def test_reconciliation_missing_account_stops_before_client(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv("FIRSTRADE_BROKER_RECONCILIATION_ENABLED", "true")
    monkeypatch.delenv("FIRSTRADE_ACCOUNT", raising=False)
    monkeypatch.setattr(main, "_runtime_settings", lambda: SimpleNamespace(runtime_target=object()))
    monkeypatch.setattr(main, "validate_reconciliation_preconditions", lambda **_kwargs: None)
    calls = []
    monkeypatch.setattr(main, "READ_ONLY_BROKER_RECONCILIATION_CLIENT_BUILDER", lambda: calls.append(True))
    response = main.app.test_client().post("/reconcile")
    assert response.status_code == 503
    assert calls == []
