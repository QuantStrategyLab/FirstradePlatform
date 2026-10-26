from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from application.firstrade_client import FirstradeCachedSessionError
from scripts import inspect_account_history_schema as module


def test_schema_exports_only_fixed_field_labels_and_types():
    private = "synthetic-private-value"
    result = module.project_schema({"items": [{"date": private, "amount": private,
        "description": private, "account": private, private: private}], "total_pages": 2})
    assert private not in json.dumps(result)
    assert result["containers"] == [{"path": "$.items", "row_count": 1,
        "known_field_types": {"date": ["str"], "description": ["str"], "amount": ["str"]},
        "unlisted_field_count": 2}]
    assert result["coverage"] == "first_page_only_not_complete_history"
    assert result["pagination_labels_present"] == ["total_pages"]


def test_missing_container_is_unknown_while_real_empty_list_is_observed():
    assert module.project_schema({"unexpected": []})["containers"] == []
    assert module.project_schema({"items": []})["containers"][0]["row_count"] == 0


@pytest.mark.parametrize("payload", [None, "private", ["private"], {"items": [{}] * 1001}])
def test_invalid_or_unbounded_schema_is_rejected(payload):
    with pytest.raises(ValueError):
        module.project_schema(payload)


@pytest.mark.parametrize("failure", [None, "cache_stale", "cache_read_failed"])
def test_cloud_reader_reuses_exact_runtime_cache_and_never_loads_login_secrets(tmp_path, monkeypatch, failure):
    runtime = {"RUNTIME_TARGET_JSON": json.dumps({"account_selector": ["synthetic-account"]}),
        "FIRSTRADE_REUSE_SESSION": "true", "FIRSTRADE_PERSIST_SESSION_CACHE": "true",
        "FIRSTRADE_GCS_STATE_BUCKET": "synthetic-state", "FIRSTRADE_SESSION_CACHE_TTL_SECONDS": "86400"}
    calls, closed = [], []
    def run(args, **kwargs):
        calls.append(args)
        assert kwargs["capture_output"] and kwargs["timeout"] == 45
        if args[1:4] == ["run", "services", "describe"]:
            value = json.dumps({"status": {"traffic": [{"percent": 100, "revisionName": "synthetic-revision"}]}})
        elif args[1:4] == ["run", "revisions", "describe"]:
            value = json.dumps({"spec": {"containers": [{"env": [{"name": k, "value": v} for k, v in runtime.items()]}]}})
        elif args[1:4] == ["secrets", "versions", "access"]:
            assert "--secret=firstrade-username" in args
            value = "synthetic-user"
        else:
            assert args[1:3] == ["storage", "cat"]
            if failure == "cache_read_failed":
                return SimpleNamespace(returncode=1, stdout="", stderr="private error must not escape")
            value = json.dumps({"saved_at": 1000, "ftat": "synthetic-token"})
        return SimpleNamespace(returncode=0, stdout=value)
    class Client:
        read_only_request_attempts = 0
        def __init__(self, credentials, *, session_cache_store):
            assert credentials.password == "" and credentials.mfa_secret == ""
            assert credentials.session_cache_ttl_seconds == 86400 and not credentials.persist_session_cache
            self.credentials, self.store = credentials, session_cache_store
        def connect_read_only(self):
            key = "sessions/" + module.hashlib.sha256(b"synthetic-user").hexdigest()[:16] + "/latest.json"
            assert self.store.read_json(key)["ftat"] == "synthetic-token"
            with pytest.raises(ValueError): self.store.write_json("x", {})
            if failure == "cache_stale":
                raise FirstradeCachedSessionError("cache_missing_or_stale")
            self.read_only_request_attempts = 2
        def get_account_history_read_only(self, account, start, end):
            assert account == "synthetic-account"
            self.read_only_request_attempts += 1
            return {"items": []}
        def close(self): closed.append(True)
    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module, "FirstradeBrokerClient", Client)
    for key, value in {"GCP_PROJECT_ID": "synthetic-project", "CLOUD_RUN_REGION": "us-east1",
        "CLOUD_RUN_SERVICE": "synthetic-service", "RUNNER_TEMP": str(tmp_path)}.items():
        monkeypatch.setenv(key, value)
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    if failure == "cache_read_failed":
        with pytest.raises(ValueError, match="history_cloud_read_unavailable"):
            module.execute(end.isoformat(), end.isoformat())
        assert closed == []
    else:
        result = module.execute(end.isoformat(), end.isoformat())
        assert result["status"] == ("stopped" if failure else "inspected")
        assert result["broker_request_attempts"] == (0 if failure else 3)
        assert result["cache_writes"] == result["business_writes"] == 0
        assert closed == [True]
    assert len(calls) == 4


def test_invalid_window_stops_before_any_cloud_or_broker_call(monkeypatch):
    monkeypatch.setattr(module.subprocess, "run", lambda *_a, **_k: pytest.fail("no reads expected"))
    with pytest.raises(ValueError):
        module.execute("2026-01-01", "2026-02-01")


def test_reader_main_cannot_read_credentials_or_financial_bodies_on_local_host(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.setattr(module, "execute", lambda *_a: pytest.fail("cloud-only reader must not run"))
    assert module.main() == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "history_cloud_manual_main_once_required"
