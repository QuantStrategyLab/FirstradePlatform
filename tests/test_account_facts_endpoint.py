from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

pytest.importorskip("flask")

import main
from application.account_facts_readonly import AccountFactsUnavailable
from application.account_facts_publisher import AccountFactsPublishError
from application.firstrade_client import FirstradeCachedSessionError


def test_cached_failure_logs_correlate_without_changing_response(monkeypatch, capsys):
    from scripts.diagnose_snapshot_request_logs import summarize
    from tests.test_snapshot_log_diagnostic import PROJECT, SERVICE, scheduler, request as request_log

    _configure(monkeypatch)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PROJECT)
    def failed_client():
        raise FirstradeCachedSessionError("read_http_error", stage="userinfo", http_status=401)
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", failed_client)
    trace_id = "A" * 32
    response = main.app.test_client().post(
        "/account-facts-sync", headers={"Authorization": "Bearer synthetic-facts-token",
                                        "X-Cloud-Trace-Context": trace_id + "/123;o=1"})
    assert response.status_code == 503
    assert response.json == {"ok": False, "error": "readonly_client_unavailable"}
    line = capsys.readouterr().out.strip()
    payload = json.loads(line)
    assert payload["recovery_action"] == "manual_session_renewal_required"
    trace = payload.pop("logging.googleapis.com/trace")
    entry = {"resource": {"type": "cloud_run_revision", "labels": {
        "project_id": PROJECT, "location": "us-central1", "service_name": SERVICE}},
        "trace": trace, "jsonPayload": payload}
    result = summarize([scheduler(), request_log()], [entry], PROJECT, SERVICE, "us-central1")
    assert result["app_reason"] == "read_http_error"
    assert result["app_stage"] == "userinfo"
    assert result["app_http_status"] == 401
    assert "synthetic-facts-token" not in line
    assert "synthetic-native-id" not in line


@pytest.mark.parametrize("reason,stage,status,renewal", [
    ("cache_stale", "cache", None, True),
    ("cache_missing", "cache", None, True),
    ("read_http_error", "userinfo", 401, True),
    ("read_http_error", "userinfo", 403, False),
    ("read_http_error", "userinfo", 503, False),
    ("cache_read_failed", "cache", None, False),
])
def test_cached_failure_requires_separate_manual_auth_flow(
    monkeypatch, capsys, reason, stage, status, renewal,
):
    _configure(monkeypatch)
    calls = []
    def fail_client():
        calls.append("cached_read")
        raise FirstradeCachedSessionError(reason, stage=stage, http_status=status)
    def forbidden(*args, **kwargs):
        pytest.fail("failed cached read must not renew or publish")
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", fail_client)
    monkeypatch.setattr(main, "run_forced_session_renewal", forbidden)
    monkeypatch.setattr(main, "publish_firstrade_account_snapshot", forbidden)
    response = main.app.test_client().post("/account-facts-sync", headers={
        "Authorization": "Bearer synthetic-facts-token"})
    assert response.status_code == 503
    assert response.json == {"ok": False, "error": "readonly_client_unavailable"}
    assert calls == ["cached_read"]
    record = json.loads(capsys.readouterr().out)
    assert (record.get("recovery_action") == "manual_session_renewal_required") is renewal


@pytest.mark.parametrize("header,project", [(None, "example-project"), ("bad", "example-project"),
                                             ("a" * 32, None),
                                             ("a" * 32 + "/1;o=1", "bad/project")])
def test_failure_omits_invalid_or_absent_trace(monkeypatch, capsys, header, project):
    _configure(monkeypatch)
    if project is None:
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    else:
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", project)
    def failed_client():
        raise FirstradeCachedSessionError("cache_stale", stage="cache")
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", failed_client)
    headers = {"Authorization": "Bearer synthetic-facts-token"}
    if header is not None:
        headers["X-Cloud-Trace-Context"] = header
    response = main.app.test_client().post("/account-facts-sync", headers=headers)
    assert response.status_code == 503
    assert "logging.googleapis.com/trace" not in json.loads(capsys.readouterr().out)


def test_failure_trace_is_request_local_under_concurrency(monkeypatch, capsys):
    _configure(monkeypatch)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "example-project")
    def failed_client():
        raise FirstradeCachedSessionError("cache_stale", stage="cache")
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", failed_client)
    def invoke(char):
        return main.app.test_client().post("/account-facts-sync", headers={
            "Authorization": "Bearer synthetic-facts-token", "X-Cloud-Trace-Context": char * 32}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(invoke, ["a", "b"])) == [503, 503]
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert {r["logging.googleapis.com/trace"] for r in rows} == {
        "projects/example-project/traces/" + c * 32 for c in ["a", "b"]}



def test_log_fields_reject_untrusted_values(monkeypatch, capsys):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "example-project")
    with main.app.test_request_context(headers={"X-Cloud-Trace-Context": "a" * 32,
                                                "Authorization": "Bearer private-secret"}):
        main._log_account_facts_failure("private-secret", "private-stage", "401")
    output = capsys.readouterr().out
    record = json.loads(output)
    assert record["reason"] == "unknown"
    assert record["stage"] == "unclassified"
    assert record["http_status"] is None
    assert "private-secret" not in output
    assert "private-stage" not in output



@pytest.mark.parametrize("reason", ["provider_value_invalid", "snapshot_invalid", "private-secret"])
def test_unlisted_failure_reason_is_unknown_without_changing_http(monkeypatch, capsys, reason):
    from scripts.diagnose_snapshot_request_logs import summarize
    from tests.test_snapshot_log_diagnostic import PROJECT, SERVICE, scheduler, request as request_log
    _configure(monkeypatch)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PROJECT)
    client = FakeClient()
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: client)
    def fail(*args, **kwargs):
        raise AccountFactsUnavailable(reason)
    monkeypatch.setattr(main, "collect_firstrade_account_facts", fail)
    response = main.app.test_client().post("/account-facts-sync", headers={
        "Authorization": "Bearer synthetic-facts-token", "X-Cloud-Trace-Context": "a" * 32})
    assert response.status_code == 503
    assert response.json == {"ok": False, "error": reason}
    line = capsys.readouterr().out
    payload = json.loads(line)
    assert payload['reason'] == 'unknown'
    assert reason not in line
    trace = payload.pop('logging.googleapis.com/trace')
    row = {'resource': {'type': 'cloud_run_revision', 'labels': {
        'project_id': PROJECT, 'location': 'us-central1', 'service_name': SERVICE}},
        'trace': trace, 'jsonPayload': payload}
    assert summarize([scheduler(), request_log()], [row], PROJECT, SERVICE, 'us-central1')['app_reason'] == 'unknown'



@pytest.mark.parametrize("fail_at", ["write", "flush"])
@pytest.mark.parametrize("failure,expected_status,expected_reason", [
    ("collect", 503, "provider_value_invalid"), ("publish", 502, "sync_unavailable"),
])
def test_stdout_write_failure_preserves_http_response(monkeypatch, failure, expected_status, expected_reason, fail_at):
    _configure(monkeypatch)
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: FakeClient())
    def collect(*args, **kwargs):
        if failure == "collect":
            raise AccountFactsUnavailable(expected_reason)
        return {}
    def publish(*args, **kwargs):
        raise AccountFactsPublishError(expected_reason)
    monkeypatch.setattr(main, "collect_firstrade_account_facts", collect)
    monkeypatch.setattr(main, "build_firstrade_account_snapshot", lambda *args, **kwargs: {})
    monkeypatch.setattr(main, "publish_firstrade_account_snapshot", publish)
    class BrokenOutput:
        def write(self, value):
            if fail_at == "write":
                raise OSError("private-error")
        def flush(self):
            if fail_at == "flush":
                raise OSError("private-error")
    with monkeypatch.context() as patch:
        patch.setattr(main.sys, "stdout", BrokenOutput())
        response = main.app.test_client().post("/account-facts-sync", headers={
            "Authorization": "Bearer synthetic-facts-token"})
    assert response.status_code == expected_status
    assert response.json == {"ok": False, "error": expected_reason}


def _configure(monkeypatch, **overrides):
    values = {
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED": "true",
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_URL": "https://qsl-strategy-switch-console.pigbibi.workers.dev/api/account-facts/sync",
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN": "synthetic-facts-token",
        "FIRSTRADE_ACCOUNT_FACTS_TARGET_ID": "firstrade-live-synthetic",
        "FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID": "a" * 64,
        "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_KEY": "firstrade-main",
        "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE": "us",
        "FIRSTRADE_ACCOUNT": "synthetic-native-id",
    }
    values.update(overrides)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    for name in (
        "ACCOUNT_FACTS_SYNC_TOKEN",
        "IBKR_ACCOUNT_FACTS_SYNC_TOKEN",
        "SCHWAB_ACCOUNT_FACTS_SYNC_TOKEN",
        "BINANCE_ACCOUNT_FACTS_SYNC_TOKEN",
        "EXECUTION_EVIDENCE_SYNC_TOKEN",
        "STRATEGY_SWITCH_SYNC_TOKEN",
        "RECONCILIATION_RECOVERY_SYNC_TOKEN",
        "RECONCILIATION_RECOVERY_CONTROLLER_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(main, "_runtime_settings", lambda: SimpleNamespace(runtime_target=SimpleNamespace(
        platform_id="firstrade",
        account_selector=("synthetic-native-id",),
        account_scope="us",
    )))


class FakeClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_sync_endpoint_reads_once_closes_and_returns_only_safe_ack(monkeypatch):
    _configure(monkeypatch)
    client = FakeClient()
    client_builds = []
    payloads = []
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: (client_builds.append(1), client)[1])
    observation = {
        "status": "available",
        "platform": "firstrade",
        "account_selector_status": "matched",
        "broker_account_id": "synthetic-native-id",
        "observed_started_at": "2026-10-08T08:00:00+00:00",
        "observed_finished_at": "2026-10-08T08:00:03+00:00",
        "balances": {"currency": "USD", "provider_equity": "100.00", "cash_balance": "50.00"},
        "positions": [{"symbol": "MUST_NOT_BE_SENT"}],
    }
    collector_calls = []

    def collect_balances_only(*_args, **kwargs):
        collector_calls.append(kwargs)
        return observation

    monkeypatch.setattr(main, "collect_firstrade_account_facts", collect_balances_only)
    monkeypatch.setattr(
        main,
        "publish_firstrade_account_snapshot",
        lambda payload, cfg: payloads.append((payload, cfg)),
    )

    response = main.app.test_client().post(
        "/account-facts-sync",
        headers={"Authorization": "Bearer synthetic-facts-token"},
        data="{}",
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "stored": True}
    assert len(client_builds) == 1
    assert collector_calls == [{"expected_account": "synthetic-native-id", "include_positions": False}]
    assert client.closed is True
    assert len(payloads) == 1
    posted, cfg = payloads[0]
    assert posted["broker_account_id"] == cfg.account_id
    assert "positions" not in posted
    assert "MUST_NOT_BE_SENT" not in response.get_data(as_text=True)
    assert "synthetic-native-id" not in response.get_data(as_text=True)



def test_sync_endpoint_accepts_cloud_scheduler_oidc_bearer(monkeypatch):
    _configure(monkeypatch)
    client = FakeClient()
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: client)
    observation = {
        "status": "available",
        "platform": "firstrade",
        "account_selector_status": "matched",
        "broker_account_id": "synthetic-native-id",
        "observed_started_at": "2026-10-08T08:00:00+00:00",
        "observed_finished_at": "2026-10-08T08:00:03+00:00",
        "balances": {"currency": "USD", "provider_equity": "100.00", "cash_balance": "50.00"},
    }
    monkeypatch.setattr(main, "collect_firstrade_account_facts", lambda *_args, **_kwargs: observation)
    published = []
    monkeypatch.setattr(main, "publish_firstrade_account_snapshot", lambda payload, _config: published.append(payload))

    response = main.app.test_client().post(
        "/account-facts-sync",
        headers={
            "Authorization": "Bearer aaa.bbb.ccc",
            "User-Agent": "Google-Cloud-Scheduler",
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "stored": True}
    assert len(published) == 1
    assert client.closed is True


def test_sync_endpoint_reuses_the_unique_protected_runtime_selector(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.delenv("FIRSTRADE_ACCOUNT", raising=False)
    client = FakeClient()
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: client)
    observation = {
        "status": "available",
        "platform": "firstrade",
        "account_selector_status": "matched",
        "broker_account_id": "synthetic-native-id",
        "observed_started_at": "2026-10-08T08:00:00+00:00",
        "observed_finished_at": "2026-10-08T08:00:03+00:00",
        "balances": {"currency": "USD", "provider_equity": "100.00", "cash_balance": None},
    }
    monkeypatch.setattr(main, "collect_firstrade_account_facts", lambda *_args, **_kwargs: observation)
    published = []
    monkeypatch.setattr(main, "publish_firstrade_account_snapshot", lambda payload, _config: published.append(payload))

    response = main.app.test_client().post(
        "/account-facts-sync", headers={"Authorization": "Bearer synthetic-facts-token"}
    )

    assert response.status_code == 200
    assert len(published) == 1
    assert published[0]["broker_account_id"] == "synthetic-native-id"
    assert client.closed is True


@pytest.mark.parametrize(
    ("overrides", "headers", "expected_status", "expected_error"),
    [
        ({"FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED": "false"}, {}, 404, "disabled"),
        ({}, {"Authorization": "Bearer wrong-token"}, 401, "unauthorized"),
        ({"FIRSTRADE_ACCOUNT_FACTS_TARGET_ID": ""}, {"Authorization": "Bearer synthetic-facts-token"}, 503, "configuration_incomplete"),
        ({"FIRSTRADE_ACCOUNT_FACTS_SYNC_URL": "https://other.example/api/account-facts/sync"}, {"Authorization": "Bearer synthetic-facts-token"}, 503, "destination_not_approved"),
    ],
)
def test_off_auth_or_invalid_configuration_causes_zero_broker_io(
    monkeypatch, overrides, headers, expected_status, expected_error
):
    _configure(monkeypatch, **overrides)
    calls = []
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: calls.append("build"))

    response = main.app.test_client().post("/account-facts-sync", headers=headers)

    assert response.status_code == expected_status
    assert response.get_json() == {"ok": False, "error": expected_error}
    assert calls == []


@pytest.mark.parametrize(
    "runtime_target",
    [
        None,
        SimpleNamespace(platform_id="other", account_selector=("synthetic-native-id",), account_scope="us"),
        SimpleNamespace(platform_id="firstrade", account_selector=("synthetic-native-id",), account_scope="other"),
    ],
)
def test_runtime_target_mismatch_is_rejected_before_broker_io(monkeypatch, runtime_target):
    _configure(monkeypatch)
    monkeypatch.setattr(main, "_runtime_settings", lambda: SimpleNamespace(runtime_target=runtime_target))
    calls = []
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: calls.append("build"))

    response = main.app.test_client().post(
        "/account-facts-sync", headers={"Authorization": "Bearer synthetic-facts-token"}
    )

    assert response.status_code == 409
    assert response.get_json() == {"ok": False, "error": "runtime_target_mismatch"}
    assert calls == []


@pytest.mark.parametrize("selectors", [(), ("synthetic-native-id", "other-id"), ("",)])
def test_empty_or_multiple_runtime_selectors_are_rejected_before_broker_io(monkeypatch, selectors):
    _configure(monkeypatch)
    monkeypatch.setattr(main, "_runtime_settings", lambda: SimpleNamespace(runtime_target=SimpleNamespace(
        platform_id="firstrade", account_selector=selectors, account_scope="us"
    )))
    calls = []
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: calls.append("build"))

    response = main.app.test_client().post(
        "/account-facts-sync", headers={"Authorization": "Bearer synthetic-facts-token"}
    )

    assert response.status_code == 409
    assert response.get_json() == {"ok": False, "error": "runtime_target_account_selector_invalid"}
    assert calls == []


def test_explicit_account_environment_conflict_is_rejected_before_broker_io(monkeypatch):
    _configure(monkeypatch, FIRSTRADE_ACCOUNT="different-synthetic-id")
    calls = []
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: calls.append("build"))

    response = main.app.test_client().post(
        "/account-facts-sync", headers={"Authorization": "Bearer synthetic-facts-token"}
    )

    assert response.status_code == 409
    assert response.get_json() == {"ok": False, "error": "account_identity_mismatch"}
    assert calls == []


def test_collection_failure_is_safe_and_client_still_closes(monkeypatch):
    _configure(monkeypatch)
    client = FakeClient()
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: client)
    monkeypatch.setattr(
        main,
        "collect_firstrade_account_facts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AccountFactsUnavailable("balances_unavailable")),
    )
    published = []
    monkeypatch.setattr(main, "publish_firstrade_account_snapshot", lambda *_args: published.append(True))

    response = main.app.test_client().post(
        "/account-facts-sync", headers={"Authorization": "Bearer synthetic-facts-token"}
    )

    assert response.status_code == 503
    assert response.get_json() == {"ok": False, "error": "balances_unavailable"}
    assert client.closed is True
    assert published == []


def test_sync_endpoint_falls_back_to_sole_broker_account_when_selector_unmatched(monkeypatch):
    _configure(monkeypatch, FIRSTRADE_ACCOUNT="firstrade")
    monkeypatch.setattr(main, "_runtime_settings", lambda: SimpleNamespace(runtime_target=SimpleNamespace(
        platform_id="firstrade",
        account_selector=("firstrade",),
        account_scope="us",
    )))

    class SoleAccountClient(FakeClient):
        def account_numbers(self):
            return ["synthetic-native-id"]

    client = SoleAccountClient()
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: client)
    collector_calls = []

    def collect(_client, *, expected_account, include_positions):
        collector_calls.append({"expected_account": expected_account, "include_positions": include_positions})
        return {
            "status": "available",
            "platform": "firstrade",
            "account_selector_status": "matched",
            "broker_account_id": expected_account,
            "observed_started_at": "2026-10-08T08:00:00+00:00",
            "observed_finished_at": "2026-10-08T08:00:03+00:00",
            "balances": {"currency": "USD", "provider_equity": "100.00", "cash_balance": "50.00"},
        }

    monkeypatch.setattr(main, "collect_firstrade_account_facts", collect)
    published = []
    monkeypatch.setattr(main, "publish_firstrade_account_snapshot", lambda payload, cfg: published.append((payload, cfg)))

    response = main.app.test_client().post(
        "/account-facts-sync",
        headers={"Authorization": "Bearer synthetic-facts-token"},
    )
    assert response.status_code == 200
    assert collector_calls == [{"expected_account": "synthetic-native-id", "include_positions": False}]
    assert published[0][0]["broker_account_id"] == "synthetic-native-id"
    assert published[0][1].account_id == "synthetic-native-id"
    assert client.closed is True


def test_sync_reads_positions_only_with_explicit_currency_configuration(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setenv("FIRSTRADE_ACCOUNT_FACTS_POSITION_CURRENCY", "EUR")
    client = FakeClient()
    calls, posted = [], []
    monkeypatch.setattr(main, "READ_ONLY_ACCOUNT_FACTS_CLIENT_BUILDER", lambda: client)

    def collect(_client, **kwargs):
        calls.append(kwargs)
        return {
            "status": "available", "platform": "firstrade", "account_selector_status": "matched",
            "broker_account_id": "synthetic-native-id",
            "observed_started_at": "2026-10-08T08:00:00+00:00", "observed_finished_at": "2026-10-08T08:00:03+00:00",
            "balances": {"currency": "USD", "provider_equity": "100", "cash_balance": "50"},
            "positions_status": "available",
            "positions": [{"symbol": "SYNTH", "quantity": "1", "market_value": "50", "currency": None}],
        }

    monkeypatch.setattr(main, "collect_firstrade_account_facts", collect)
    monkeypatch.setattr(main, "publish_firstrade_account_snapshot", lambda body, _cfg: posted.append(body))
    response = main.app.test_client().post("/account-facts-sync", headers={"Authorization": "Bearer synthetic-facts-token"})
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "stored": True}
    assert calls == [{"expected_account": "synthetic-native-id", "include_positions": True}]
    assert posted[0]["broker_reported_positions"][0]["currency"] == "EUR"
    assert posted[0]["broker_reported_positions"][0]["currency_source"] == "configured"
    assert client.closed is True
    assert "SYNTH" not in response.get_data(as_text=True)
