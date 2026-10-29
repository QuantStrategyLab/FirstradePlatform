from datetime import datetime, timezone
import json

from scripts.project_runtime_daily import PLATFORM, project_runtime_daily


def test_only_bound_reports_project_and_missing_never_claims_no_runs():
    anchor = "a" * 64
    report = {"platform": PLATFORM, "service_name": "synthetic-service", "strategy_profile": "synthetic-profile",
              "account_scope": "live", "run_id": "private-run", "started_at": "2026-10-29T18:00:00Z",
              "finished_at": "2026-10-29T18:01:00Z", "dry_run": False, "errors": [], "status": "ok",
              "runtime_target": {"execution_mode": "live"},
              "account_facts_source_binding_id": anchor,
              "external_cash_flow_interval": {"account_scope_sha256": anchor}}
    kwargs = {"target_key": "synthetic-service|synthetic-profile|live", "account_binding": anchor,
              "business_date": "2026-10-29", "observed_at": datetime(2026, 10, 29, 20, tzinfo=timezone.utc)}
    result = project_runtime_daily([report], **kwargs)
    assert len(result["records"][0]["runs"]) == 1
    assert result["records"][0]["runs"][0]["activity"] == "unknown"
    assert result["records"][0]["completeness"] == "incomplete"
    assert anchor not in json.dumps(result) and "private-run" not in json.dumps(result)
    field = "account_facts_source_binding_id" if PLATFORM == "firstrade" else "external_cash_flow_interval"
    report[field] = None
    unmatched = project_runtime_daily([report], **kwargs)
    assert unmatched["records"][0]["runs"] == [] and len(unmatched["unmatched_reports"]) == 1
    assert unmatched["records"][0]["schedule"]["state"] == "unevaluable"
    assert unmatched["records"][0]["fills"]["count"] is None


def test_publish_once_requires_exact_ack_and_never_retries(monkeypatch):
    from scripts import publish_runtime_daily as publisher

    monkeypatch.setattr(publisher, "_binding", lambda _env, _target: ("synthetic-key", "a" * 64, "b" * 64))
    monkeypatch.setattr(publisher, "_FACTS_URL", "https://example.test/api/account-facts/sync")
    report = {"platform": PLATFORM, "service_name": "synthetic-service", "strategy_profile": "synthetic-profile",
              "account_scope": "live", "run_id": "private-run", "started_at": "2026-10-29T18:00:00Z",
              "finished_at": "2026-10-29T18:01:00Z", "dry_run": False, "errors": [], "status": "ok",
              "runtime_target": {"execution_mode": "live"}, "account_facts_source_binding_id": "a" * 64,
              "external_cash_flow_interval": {"account_scope_sha256": "a" * 64}}
    monkeypatch.setattr(publisher, "_read_reports", lambda *_args: ([report], None))
    env = {"RUNTIME_TARGET_JSON": json.dumps({"service_name": "synthetic-service", "strategy_profile": "synthetic-profile", "account_scope": "live"}),
           "EXECUTION_EVIDENCE_SYNC_TOKEN": "synthetic-token", "EXECUTION_EVIDENCE_SYNC_URL": "https://example.test/api/execution-evidence/sync",
           "RUNTIME_DAILY_BUSINESS_DATE": "2026-10-29"}
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self, _limit): return b"{}"
    class Opener:
        def __init__(self): self.calls = 0
        def open(self, request, timeout):
            self.calls += 1
            assert request.method == "POST" and timeout == 20
            return Response()
    opener = Opener()
    result = publisher.publish_once(env, now=datetime(2026, 10, 29, 20, tzinfo=timezone.utc), opener=opener)
    assert result == "unconfirmed" and opener.calls == 1


def test_matching_ack_is_only_success_and_nested_identity_mismatch_excluded(monkeypatch):
    from scripts import publish_runtime_daily as publisher

    binding = "a" * 64
    monkeypatch.setattr(publisher, "_binding", lambda _env, _target: ("synthetic-key", binding, "b" * 64))
    monkeypatch.setattr(publisher, "_FACTS_URL", "https://example.test/api/account-facts/sync")
    report = {"platform": PLATFORM, "service_name": "synthetic-service", "strategy_profile": "synthetic-profile",
              "account_scope": "live", "run_id": "private-run", "started_at": "2026-10-29T18:00:00Z",
              "finished_at": "2026-10-29T18:01:00Z", "dry_run": False, "errors": [], "status": "ok",
              "runtime_target": {"execution_mode": "live"}, "account_facts_source_binding_id": "a" * 64,
              "external_cash_flow_interval": {"account_scope_sha256": "a" * 64}}
    monkeypatch.setattr(publisher, "_read_reports", lambda *_args: ([report], None))
    env = {"RUNTIME_TARGET_JSON": json.dumps({"service_name": "synthetic-service", "strategy_profile": "synthetic-profile", "account_scope": "live"}),
           "EXECUTION_EVIDENCE_SYNC_TOKEN": "synthetic-token", "EXECUTION_EVIDENCE_SYNC_URL": "https://example.test/api/execution-evidence/sync",
           "RUNTIME_DAILY_BUSINESS_DATE": "2026-10-29"}
    now = datetime(2026, 10, 29, 20, tzinfo=timezone.utc)
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self, _limit):
            return json.dumps({"ok": True, "stored": True, "platform": PLATFORM, "account_key": "synthetic-key",
                               "target_key": "synthetic-service|synthetic-profile|live", "business_date": "2026-10-29",
                               "observed_at": "2026-10-29T20:00:00Z"}).encode()
    class Opener:
        calls = 0
        def open(self, _request, timeout):
            self.calls += 1
            assert timeout == 20
            return Response()
    opener = Opener()
    assert publisher.publish_once(env, now=now, opener=opener) == "published" and opener.calls == 1
    env["RUNTIME_DAILY_BUSINESS_DATE"] = "20261029"
    import pytest
    with pytest.raises(ValueError):
        publisher.publish_once(env, now=now, opener=opener)
    assert opener.calls == 1


def test_no_attributable_runs_or_wrong_origin_never_posts(monkeypatch):
    import pytest
    from scripts import publish_runtime_daily as publisher

    monkeypatch.setattr(publisher, "_binding", lambda _env, _target: ("synthetic-key", "a" * 64, "b" * 64))
    monkeypatch.setattr(publisher, "_FACTS_URL", "https://example.test/api/account-facts/sync")
    env = {"RUNTIME_TARGET_JSON": json.dumps({"service_name": "synthetic-service", "strategy_profile": "synthetic-profile", "account_scope": "live"}),
           "EXECUTION_EVIDENCE_SYNC_TOKEN": "synthetic-token", "EXECUTION_EVIDENCE_SYNC_URL": "https://example.test/api/execution-evidence/sync",
           "RUNTIME_DAILY_BUSINESS_DATE": "2026-10-29"}
    now = datetime(2026, 10, 29, 20, tzinfo=timezone.utc)
    class Opener:
        calls = 0
        def open(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("must not send")
    opener = Opener()
    for reports, error in (([], None), ([], "list_failed"), ([{"platform": PLATFORM}], None)):
        monkeypatch.setattr(publisher, "_read_reports", lambda *_args: (reports, error))
        assert publisher.publish_once(env, now=now, opener=opener) == "no_attributable_runs"
    assert opener.calls == 0
    env["EXECUTION_EVIDENCE_SYNC_URL"] = "https://untrusted.test/api/execution-evidence/sync"
    with pytest.raises(ValueError, match="daily_origin_invalid"):
        publisher.publish_once(env, now=now, opener=opener)
    assert opener.calls == 0


def test_redirect_is_not_followed_and_unknown_is_not_retried(monkeypatch):
    from urllib.error import HTTPError
    from scripts import publish_runtime_daily as publisher

    monkeypatch.setattr(publisher, "_binding", lambda _env, _target: ("synthetic-key", "a" * 64, "b" * 64))
    monkeypatch.setattr(publisher, "_FACTS_URL", "https://example.test/api/account-facts/sync")
    report = {"platform": PLATFORM, "service_name": "synthetic-service", "strategy_profile": "synthetic-profile",
              "account_scope": "live", "run_id": "private-run", "started_at": "2026-10-29T18:00:00Z",
              "finished_at": "2026-10-29T18:01:00Z", "dry_run": False, "errors": [], "status": "ok",
              "runtime_target": {"execution_mode": "live"}, "account_facts_source_binding_id": "a" * 64,
              "external_cash_flow_interval": {"account_scope_sha256": "a" * 64}}
    monkeypatch.setattr(publisher, "_read_reports", lambda *_args: ([report], None))
    env = {"RUNTIME_TARGET_JSON": json.dumps({"service_name": "synthetic-service", "strategy_profile": "synthetic-profile", "account_scope": "live"}),
           "EXECUTION_EVIDENCE_SYNC_TOKEN": "synthetic-token", "EXECUTION_EVIDENCE_SYNC_URL": "https://example.test/api/execution-evidence/sync",
           "RUNTIME_DAILY_BUSINESS_DATE": "2026-10-29"}
    class Redirect:
        calls = 0
        def open(self, request, timeout):
            self.calls += 1
            raise HTTPError(request.full_url, 302, "redirect", {}, None)
    opener = Redirect()
    assert publisher._NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.test") is None
    assert publisher.publish_once(env, now=datetime(2026, 10, 29, 20, tzinfo=timezone.utc), opener=opener) == "unconfirmed"
    assert opener.calls == 1
