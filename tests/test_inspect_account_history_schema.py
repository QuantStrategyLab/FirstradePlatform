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


def test_upstream_documented_report_date_is_recognized_without_exporting_value():
    result = module.project_schema({"items": [{"report_date": "synthetic-private-date"}]})
    assert result["containers"][0]["known_field_types"] == {"report_date": ["str"]}
    assert result["containers"][0]["unlisted_field_count"] == 0
    assert "synthetic-private-date" not in json.dumps(result)


def test_public_implementations_native_labels_do_not_export_values_or_dynamic_keys():
    result = module.project_schema({"items": [{"trade_price": 1.234, "trans_str": "private-kind",
        "descriptionArray": ["private-description"], "private-dynamic-key": "private-value"}]})
    assert result["containers"][0]["known_field_types"] == {
        "trade_price": ["float"], "trans_str": ["str"], "descriptionArray": ["list"]}
    assert result["containers"][0]["unlisted_field_count"] == 1
    assert "private" not in json.dumps(result)


def test_pagination_preserves_zero_without_claiming_complete_history():
    result = module.project_schema({"items": [], "page": 1, "per_page": 1000, "total": 0, "error": ""})
    assert result["pagination_counts"] == {"page": 1, "per_page": 1000, "total": 0}
    assert result["provider_error_present"] is False
    assert result["coverage"] == "first_page_only_not_complete_history"
    unknown = module.project_schema({"items": []})
    assert unknown["pagination_counts"] == dict.fromkeys(("page", "per_page", "total"))
    assert unknown["provider_error_present"] is None


def test_archive_date_checks_count_only_unambiguous_formats_and_requested_window():
    rows = [{"report_date": value} for value in (
        "2026-09-03", "20260904", "2026/09/05", "09/13/2026", "13/09/2026",
        "09/10/2026", "2026-10-01", "2026-02-30", "synthetic-private-date", None, 20260901,
    )] + [{}]
    schema = module.project_schema({"items": rows}, start="2026-09-01", end="2026-09-30")
    dates = schema["semantic_checks"]["containers"][0]["report_date"]
    assert dates == {"present_count": 11, "missing_count": 1, "type_counts": {"str": 9, "NoneType": 1, "int": 1},
        "parsed_count": 6, "invalid_count": 1, "unknown_format_count": 1, "ambiguous_count": 1,
        "non_string_count": 2, "in_window_count": 5, "out_of_window_count": 1,
        "parsed_format_counts": {"iso_date": 2, "compact_date": 1, "year_first_slash": 1,
                                 "unambiguous_numeric_slash": 2}}
    exported = json.dumps(schema)
    assert "synthetic-private-date" not in exported
    assert "2026-09-03" not in exported and "2026-10-01" not in exported
    assert schema["semantic_checks"]["transaction_qualification"] == "not_qualified"


def test_archive_currency_and_id_checks_never_export_values_or_claim_stable_identity():
    rows = [
        {"currency": "USD", "id": "synthetic-private-id", "transaction_id": "synthetic-private-tx"},
        {"currency": "USD", "id": "synthetic-private-id"},
        {"currency": "synthetic-private-currency", "id": 7},
        {"currency": "EUR", "id": " "},
        {"currency": None, "id": None},
        {"currency": True, "id": True},
        {"currency": 123, "id": 1.5},
        {"synthetic-private-field": "synthetic-private-value"},
    ]
    schema = module.project_schema({"items": rows}, start="2026-09-01", end="2026-09-30")
    checks = schema["semantic_checks"]["containers"][0]
    assert checks["currency"] == {"present_count": 7, "missing_count": 1,
        "type_counts": {"str": 4, "NoneType": 1, "bool": 1, "int": 1},
        "known_currency_counts": {"USD": 2}, "unknown_value_count": 5}
    assert checks["native_id_fields"]["id"] == {"present_count": 7, "missing_count": 1,
        "type_counts": {"str": 3, "int": 1, "NoneType": 1, "bool": 1, "float": 1},
        "nonempty_scalar_count": 3, "distinct_scalar_count": 2, "duplicate_scalar_count": 1}
    assert checks["native_id_fields"]["transaction_id"]["present_count"] == 1
    assert checks["native_id_fields"]["reference"]["present_count"] == 0
    assert checks["native_identity_stability"] == "unknown"
    exported = json.dumps(schema)
    assert "synthetic-private" not in exported
    assert "EUR" not in exported and "123" not in exported


def test_numeric_slash_interpretations_that_agree_are_one_unambiguous_date():
    schema = module.project_schema({"items": [{"report_date": "09/09/2026"}]},
        start="2026-09-01", end="2026-09-30")
    dates = schema["semantic_checks"]["containers"][0]["report_date"]
    assert dates["parsed_count"] == dates["in_window_count"] == 1
    assert dates["ambiguous_count"] == 0
    assert dates["parsed_format_counts"]["unambiguous_numeric_slash"] == 1
    assert "09/09/2026" not in json.dumps(schema)


@pytest.mark.parametrize("counts,size,consistency,coverage,expected", [
    ({"page": 1, "per_page": 1000, "total": 1}, 1, "consistent", "reported_single_page", 1),
    ({"page": 1, "per_page": 1000, "total": 0}, 0, "consistent", "reported_single_page", 0),
    ({"page": 1, "per_page": 2, "total": 3}, 2, "consistent", "more_pages_reported", 2),
    ({"page": 1, "per_page": 1000, "total": 2}, 1, "inconsistent", "unknown", 2),
    ({"page": 2, "per_page": 1000, "total": 1}, 1, "inconsistent", "unknown", None),
    ({"page": 0, "per_page": 1000, "total": 0}, 0, "inconsistent", "unknown", None),
    ({"page": 1, "per_page": 0, "total": 0}, 0, "inconsistent", "unknown", None),
    ({"page": 1, "per_page": 1000}, 0, "unknown", "unknown", None),
    ({"page": "synthetic-private-value", "per_page": 1000, "total": 1}, 1, "unknown", "unknown", None),
])
def test_archive_pagination_checks_only_report_metadata_consistency(counts, size, consistency, coverage, expected):
    schema = module.project_schema({"items": [{} for _ in range(size)], **counts},
        start="2026-09-01", end="2026-09-30")
    pagination = schema["semantic_checks"]["pagination"]
    assert pagination["row_count"] == size
    assert pagination["metadata_consistency"] == consistency
    assert pagination["page_coverage"] == coverage
    assert pagination["expected_row_count"] == expected
    assert schema["coverage"] == "first_page_only_not_complete_history"
    assert "synthetic-private-value" not in json.dumps(schema)


@pytest.mark.parametrize("extra", [
    {"total_pages": 2}, {"next_page": 2}, {"next_page": "synthetic-private-value"},
    {"pagination": {"synthetic-private-field": "synthetic-private-value"}},
])
def test_uninterpreted_pagination_hints_keep_consistency_and_coverage_unknown(extra):
    schema = module.project_schema({"items": [{}], "page": 1, "per_page": 1000, "total": 1, **extra},
        start="2026-09-01", end="2026-09-30")
    assert schema["semantic_checks"]["pagination"] == {"row_count": 1, "expected_row_count": None,
        "metadata_consistency": "unknown", "page_coverage": "unknown"}
    assert schema["coverage"] == "first_page_only_not_complete_history"
    assert "synthetic-private" not in json.dumps(schema)


@pytest.mark.parametrize("payload", [{"unexpected": []}, {"items": [], "rows": []}])
def test_missing_or_ambiguous_row_containers_never_prove_single_page_coverage(payload):
    schema = module.project_schema({**payload, "page": 1, "per_page": 1000, "total": 0},
        start="2026-09-01", end="2026-09-30")
    assert schema["semantic_checks"]["pagination"] == {"row_count": None, "expected_row_count": None,
        "metadata_consistency": "unknown", "page_coverage": "unknown"}


@pytest.mark.parametrize("start,end", [("synthetic-private-window", "2026-09-30"),
    ("2026-09-01", None), ("2026-09-30", "2026-09-01")])
def test_invalid_semantic_window_uses_a_fixed_error_without_private_values(start, end):
    with pytest.raises(ValueError, match="^history_semantic_window_invalid$"):
        module.project_schema({"items": []}, start=start, end=end)


@pytest.mark.parametrize("value", [True, False, -1, 1000001, 1.5, "synthetic-private-value", {}, []])
def test_untrusted_pagination_values_are_not_exported_as_counts(value):
    result = module.project_schema({"page": value, "per_page": value, "total": value})
    assert result["pagination_counts"] == dict.fromkeys(("page", "per_page", "total"))


def test_provider_error_text_is_reduced_to_a_flag():
    result = module.project_schema({"items": [], "error": "synthetic-private-error"})
    assert result["provider_error_present"] is True
    assert "synthetic-private-error" not in json.dumps(result)


@pytest.mark.parametrize("payload", [None, "private", ["private"], {"items": [{}] * 1001}])
def test_invalid_or_unbounded_schema_is_rejected(payload):
    with pytest.raises(ValueError):
        module.project_schema(payload)


@pytest.mark.parametrize("failure", [None, "cache_stale", "cache_read_failed", "provider_error"])
@pytest.mark.parametrize("archive_page", [False, True])
def test_cloud_reader_reuses_exact_runtime_cache_and_never_loads_login_secrets(tmp_path, monkeypatch, failure, archive_page):
    runtime = {"RUNTIME_TARGET_JSON": json.dumps({"account_selector": ["synthetic-account"]}),
        "FIRSTRADE_REUSE_SESSION": "true", "FIRSTRADE_PERSIST_SESSION_CACHE": "true",
        "FIRSTRADE_GCS_STATE_BUCKET": "synthetic-state", "FIRSTRADE_SESSION_CACHE_TTL_SECONDS": "86400"}
    calls, closed, archived = [], [], []
    class Archive:
        def __init__(self, bucket): assert bucket == "synthetic-state"
        def create(self, name, body):
            archived.append((name, json.loads(body)))
            return {"archive_verified": True, "archive_write_requests": 1}
    monkeypatch.setattr(module, "PrivatePageArchive", Archive)
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
            payload = {"items": [], "error": "synthetic-private-error" if failure == "provider_error" else ""}
            self.read_only_history_response_bytes = json.dumps(payload).encode()
            return payload
        def close(self): closed.append(True)
    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module, "FirstradeBrokerClient", Client)
    for key, value in {"GCP_PROJECT_ID": "synthetic-project", "CLOUD_RUN_REGION": "us-east1",
        "CLOUD_RUN_SERVICE": "synthetic-service", "RUNNER_TEMP": str(tmp_path),
        "GITHUB_RUN_ID": "123", "GITHUB_SHA": "a" * 40}.items():
        monkeypatch.setenv(key, value)
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    if failure == "cache_read_failed":
        with pytest.raises(ValueError, match="history_cloud_read_unavailable"):
            module.execute(end.isoformat(), end.isoformat(), archive_page=archive_page)
        assert closed == []
    else:
        result = module.execute(end.isoformat(), end.isoformat(), archive_page=archive_page)
        assert result["status"] == ("stopped" if failure else "inspected")
        assert result["broker_request_attempts"] == (0 if failure == "cache_stale" else 3)
        if failure == "provider_error":
            assert result["reason"] == "history_provider_reported_error"
            assert "synthetic-private-error" not in json.dumps(result)
        assert result["cache_writes"] == result["business_writes"] == 0
        assert closed == [True]
        if archive_page and not failure:
            assert result["archive_verified"] is True
            assert len(archived) == 1 and archived[0][0].endswith("/123-1.json")
            assert "synthetic-account" not in archived[0][0]
        else:
            assert archived == []
    assert len(calls) == 4


def test_invalid_window_stops_before_any_cloud_or_broker_call(monkeypatch):
    monkeypatch.setattr(module.subprocess, "run", lambda *_a, **_k: pytest.fail("no reads expected"))
    with pytest.raises(ValueError):
        module.execute("2026-01-01", "2026-02-01")


def test_archived_reader_never_reads_credentials_or_contacts_broker(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None): return cls(2026, 10, 27, tzinfo=timezone.utc)
    monkeypatch.setattr(module, "datetime", FixedDatetime)
    account = "synthetic-account"
    runtime = {"RUNTIME_TARGET_JSON": json.dumps({"account_selector": [account]}),
        "FIRSTRADE_REUSE_SESSION": "true", "FIRSTRADE_PERSIST_SESSION_CACHE": "true",
        "FIRSTRADE_GCS_STATE_BUCKET": "synthetic-state"}
    raw = json.dumps({"items": [{"trans_str": "synthetic-private-value", "report_date": "2026-09-15",
        "currency": "USD", "id": "synthetic-private-id"}],
        "page": 1, "per_page": 1000, "total": 1, "error": ""}).encode()
    body, _ = module.page_envelope(raw, account=account, start="2026-09-01", end="2026-09-30",
        source="a" * 40, observed_at="2026-10-27T10:00:00Z")
    expected_hash = module.hashlib.sha256(body).hexdigest()
    calls = []
    def run(args, **_kwargs):
        calls.append(args)
        if args[1:4] == ["run", "services", "describe"]:
            value = {"status": {"traffic": [{"percent": 100, "revisionName": "synthetic"}]}}
        else:
            assert args[1:4] == ["run", "revisions", "describe"]
            value = {"spec": {"containers": [{"env": [{"name": k, "value": v} for k, v in runtime.items()]}]}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(value))
    class Archive:
        def __init__(self, bucket): assert bucket == "synthetic-state"
        def read_pinned(self, name, generation, sha):
            assert name.endswith("/2026-09-01_2026-09-30/123-1.json")
            assert "synthetic-account" not in name and generation == "456" and sha == expected_hash
            return body
    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module, "PrivatePageArchive", Archive)
    monkeypatch.setattr(module, "FirstradeBrokerClient", lambda *_a, **_k: pytest.fail("broker forbidden"))
    for key, value in {"GCP_PROJECT_ID": "synthetic", "CLOUD_RUN_REGION": "synthetic",
        "CLOUD_RUN_SERVICE": "synthetic"}.items(): monkeypatch.setenv(key, value)
    result = module.execute("2026-09-01", "2026-09-30", archive_run="123",
        archive_generation="456", archive_sha256=expected_hash)
    assert len(calls) == 2 and result["archive_read_only"] is True
    assert result["broker_request_attempts"] == result["cache_reads"] == result["archive_write_requests"] == 0
    assert result["schema"]["containers"][0]["known_field_types"] == {
        "report_date": ["str"], "currency": ["str"], "id": ["str"], "trans_str": ["str"]}
    checks = result["schema"]["semantic_checks"]
    assert checks["containers"][0]["report_date"]["in_window_count"] == 1
    assert checks["containers"][0]["currency"]["known_currency_counts"] == {"USD": 1}
    assert checks["containers"][0]["native_identity_stability"] == "unknown"
    assert checks["pagination"] == {"row_count": 1, "expected_row_count": 1,
        "metadata_consistency": "consistent", "page_coverage": "reported_single_page"}
    assert result["schema"]["coverage"] == "first_page_only_not_complete_history"
    assert "synthetic-private" not in json.dumps(result)


@pytest.mark.parametrize("options", [
    {"archive_run": "123"},
    {"archive_run": "../private", "archive_generation": "456", "archive_sha256": "a" * 64},
    {"archive_run": "123", "archive_generation": "456", "archive_sha256": "a" * 64, "archive_page": True},
])
def test_archive_selector_must_be_complete_and_excludes_broker_collection(monkeypatch, options):
    monkeypatch.setattr(module.subprocess, "run", lambda *_a, **_k: pytest.fail("no reads expected"))
    with pytest.raises(module.ArchiveUnavailable, match="archive_selector_invalid"):
        module.execute("2026-09-01", "2026-09-30", **options)


def test_reader_main_cannot_read_credentials_or_financial_bodies_on_local_host(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.setattr(module, "execute", lambda *_a: pytest.fail("cloud-only reader must not run"))
    assert module.main() == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "history_cloud_manual_main_once_required"


@pytest.mark.parametrize("message,category", [
    ("PERMISSION_DENIED private-identifier", "permission_denied"),
    ("NOT_FOUND private-path", "not_found"),
    ("UNAUTHENTICATED private-token", "authentication_failed"),
    ("invalid_grant private-token", "authentication_failed"),
    ("SERVICE_DISABLED private-project", "service_disabled"),
    ("unrecognized arguments private-value", "argument_invalid"),
    ("private-error", "unclassified"),
])
def test_cloud_diagnostic_exports_closed_category_only(message, category):
    assert module.cloud_error_category(message) == category
    assert "private" not in module.cloud_error_category(message)


def test_reader_main_preserves_failed_stage_and_safe_category(monkeypatch, capsys):
    for key, value in {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
        "GITHUB_RUN_ATTEMPT": "1", "RUNNER_OS": "Linux"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(module.argparse.ArgumentParser, "parse_args", lambda _s:
        SimpleNamespace(from_date="2026-10-01", to_date="2026-10-02", archive_page=False,
                        archive_run="", archive_generation="", archive_sha256=""))
    def fail(*_args): raise module.CloudReadUnavailable("cached_username", "permission_denied")
    monkeypatch.setattr(module, "execute", fail)
    assert module.main() == 1
    assert json.loads(capsys.readouterr().out) == {"status": "stopped",
        "reason": "history_cloud_read_failed", "stage": "cached_username", "failure_category": "permission_denied"}


def test_archive_semantic_failure_cannot_put_sensitive_exception_text_in_stdout(monkeypatch, capsys):
    for key, value in {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
        "GITHUB_RUN_ATTEMPT": "1", "RUNNER_OS": "Linux"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(module.argparse.ArgumentParser, "parse_args", lambda _s:
        SimpleNamespace(from_date="2026-09-01", to_date="2026-09-30", archive_page=False,
                        archive_run="123", archive_generation="456", archive_sha256="a" * 64))
    def fail(*_args, **_kwargs):
        raise ValueError("synthetic-private-sensitive-field-and-value")
    monkeypatch.setattr(module, "execute", fail)
    assert module.main() == 1
    output = capsys.readouterr().out
    assert "synthetic-private" not in output
    assert json.loads(output) == {"status": "stopped", "reason": "history_schema_inspection_unavailable"}
