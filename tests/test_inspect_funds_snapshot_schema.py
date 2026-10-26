import json
from contextlib import contextmanager
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from scripts import inspect_funds_snapshot_schema as inspector
from scripts.inspect_funds_snapshot_schema import main, project_schema


def test_schema_never_exports_financial_values_or_identity():
    result = project_schema({
        "as_of": "2026-01-02T03:04:05Z", "account": "PRIVATE_ACCOUNT",
        "balance_metrics": {"cash.available": 9876.543, "buying_power": 5432.10,
                            "account.total_value": 1234.56, "cash.12345678": 42,
                            "secret_cash": "PRIVATE_SECRET", "cash.<script>": 100},
        "positions": [{"symbol": "PRIVATE_HOLDING"}],
    })
    assert result["balance_field_names"] == ["buying_power", "cash.available"]
    assert result["redacted_field_count"] == 4
    assert result["positions_present"] is True
    rendered = json.dumps(result)
    assert all(s not in rendered for s in ["9876", "5432", "PRIVATE", "12345678"])


@pytest.mark.parametrize("snapshot", [None, [], {}, {"balance_metrics": []}])
def test_missing_schema_is_not_reported_as_success(snapshot):
    with pytest.raises(ValueError):
        project_schema(snapshot)


def test_invalid_observation_text_is_not_echoed():
    result = project_schema({"as_of": "PRIVATE_TOKEN", "balance_metrics": {}})
    assert result["observed_at"] is None
    assert result["positions_present"] is False


def test_unexpected_exception_is_not_printed(monkeypatch, capsys):
    def fail():
        raise RuntimeError("PRIVATE_TOKEN")
    monkeypatch.setattr("scripts.inspect_funds_snapshot_schema.inspect_existing_snapshots", fail)
    assert main() == 1
    assert json.loads(capsys.readouterr().out) == {"status": "inspection_unavailable"}


PREFIX = "private-prefix/accounts/"
ACCOUNT = "synthetic-account"
NAMESPACE = "synthetic_account"


def archive(day="2026/01/02", stamp="20260102T030405Z", account=NAMESPACE):
    return {"name": f"{PREFIX}{account}/funds/history/{day}/{stamp}.json", "generation": "123"}


def snapshot():
    return {
        "as_of": "2026-01-02T03:04:05Z", "account": ACCOUNT,
        "balance_metrics": {"cash_balance": 9876.543, "provider_equity": "5432.10",
                            "PRIVATE_SECRET": 100, "private.account.123456": 50},
        "account_summaries": [{"private": "PRIVATE_HOLDING"}],
    }


def test_history_schema_keeps_identity_comparisons_and_never_values():
    result = inspector.project_history(snapshot(), account=NAMESPACE, archive_date="2026-01-02",
                                       configured_account=ACCOUNT)
    assert result["storage_namespace_matches"] is True
    assert result["storage_account_matches"] is False
    assert result["configured_account_matches"] is True
    assert result["observation_date_matches_archive"] is True
    assert result["known_metric_types"] == {"cash_balance": "number", "provider_equity": "string"}
    assert result["unlisted_metric_count"] == 2
    assert len(result["canonical_snapshot_sha256"]) == 64
    changed = snapshot()
    changed["balance_metrics"]["cash_balance"] = 99
    changed_result = inspector.project_history(changed, account=NAMESPACE, archive_date="2026-01-02",
                                               configured_account=ACCOUNT)
    assert result["canonical_snapshot_sha256"] != changed_result["canonical_snapshot_sha256"]
    rendered = json.dumps(result)
    assert all(value not in rendered for value in (ACCOUNT, NAMESPACE, "PRIVATE", "123456", "9876", "5432"))


def test_history_missing_selector_or_identity_stays_unknown():
    result = inspector.project_history(snapshot(), account=NAMESPACE, archive_date="2026-01-03",
                                       configured_account=None)
    assert result["configured_account_available"] is False
    assert result["configured_account_matches"] is None
    assert result["observation_date_matches_archive"] is False
    missing = snapshot()
    missing["account"] = None
    missing["as_of"] = "PRIVATE_SECRET"
    result = inspector.project_history(missing, account=NAMESPACE, archive_date="2026-01-02",
                                       configured_account=ACCOUNT)
    assert result["identity_present"] is False
    assert result["storage_namespace_matches"] is None
    assert result["configured_account_matches"] is None
    assert result["observation_date_matches_archive"] is None


@pytest.mark.parametrize("listing", [
    {"items": [archive()], "nextPageToken": "PRIVATE_SECRET"},
    {"items": [archive(), archive()]},
    {"items": []},
    {"items": [archive(day="2026/02/30", stamp="20260230T030405Z")]},
    {"items": [archive(stamp="20260103T030405Z")]},
    {"items": [{**archive(), "generation": "PRIVATE_SECRET"}]},
    {"items": [{**archive(), "name": "other-prefix/accounts/synthetic/funds/history/file.json"}]},
    {"items": [{**archive(), "name": PREFIX + "synthetic/funds/history/invalid.json"}]},
    {"items": [archive(stamp=f"20260102T0304{i:02}Z") for i in range(65)]},
    {"items": [archive(account=f"synthetic{i}") for i in range(11)]},
])
def test_history_selection_rejects_incomplete_ambiguous_or_unbounded_listing(listing):
    with pytest.raises(ValueError):
        inspector.history_objects(listing, PREFIX)


def test_history_selection_ignores_latest_and_retains_actual_weekend_archive():
    sunday = archive(day="2026/01/04", stamp="20260104T030405Z")
    objects = inspector.history_objects({"items": [sunday, {"name": PREFIX + NAMESPACE + "/funds/latest.json"}]}, PREFIX)
    assert len(objects) == 1
    assert objects[0][2] == "2026-01-04"


def install_cloud_fakes(monkeypatch, listing, *, runtime_target=False, reports=False):
    requests = []
    for key in ("GCP_PROJECT_ID", "CLOUD_RUN_REGION", "CLOUD_RUN_SERVICE"):
        monkeypatch.setenv(key, "private-placeholder")

    def gcloud(args, **kwargs):
        if args[1:3] == ["auth", "print-access-token"]:
            return "PRIVATE_TOKEN"
        if args[1:4] == ["run", "services", "describe"]:
            return json.dumps({"status": {"traffic": [{"percent": 100, "revisionName": "private-revision"}]}})
        assert args[1:4] == ["run", "revisions", "describe"]
        env = {"FIRSTRADE_GCS_STATE_BUCKET": "private-bucket", "FIRSTRADE_STATE_PREFIX": "private-prefix",
               "FIRSTRADE_ACCOUNT": ACCOUNT}
        if reports:
            del env["FIRSTRADE_GCS_STATE_BUCKET"]
            env["EXECUTION_REPORT_GCS_URI"] = "gs://private-bucket/private-reports/"
        if runtime_target:
            del env["FIRSTRADE_ACCOUNT"]
            env["RUNTIME_TARGET_JSON"] = json.dumps({"account_selector": [ACCOUNT]})
        return json.dumps({"spec": {"containers": [{"env": [{"name": k, "value": v} for k, v in env.items()]}]}})

    @contextmanager
    def urlopen(request, **kwargs):
        from io import BytesIO
        assert request.get_method() == "GET"
        parsed = urlparse(request.full_url)
        assert parsed.hostname == "storage.googleapis.com"
        assert request.get_header("Authorization") == "Bearer PRIVATE_TOKEN"
        query = parse_qs(parsed.query)
        requests.append((unquote(parsed.path), query))
        if "alt" in query:
            assert query == {"alt": ["media"], "generation": ["123"]}
            data = {"account": ACCOUNT, "summary": {"orders_previewed": []}} if reports else snapshot()
        else:
            assert query["maxResults"] == ["1000"]
            assert query["prefix"] == ["private-reports/" if reports else PREFIX]
            data = listing
        yield BytesIO(json.dumps(data).encode())

    monkeypatch.setattr(inspector.subprocess, "check_output", gcloud)
    monkeypatch.setattr(inspector.urllib.request, "urlopen", urlopen)
    return requests


def test_history_transport_reads_only_generation_pinned_private_gcs_objects(monkeypatch):
    requests = install_cloud_fakes(monkeypatch, {"items": [archive()]})
    result = inspector.inspect_existing_snapshots(history=True)
    assert len(requests) == 2
    assert result["snapshot_count"] == 1
    assert result["broker_requests"] == result["mutations"] == 0
    assert result["snapshots"][0]["configured_account_matches"] is True
    assert all(secret not in json.dumps(result) for secret in (ACCOUNT, PREFIX, "PRIVATE", "9876"))


def test_incomplete_history_listing_fetches_no_financial_body(monkeypatch):
    requests = install_cloud_fakes(monkeypatch, {"items": [archive()], "nextPageToken": "PRIVATE"})
    with pytest.raises(ValueError, match="listing_incomplete"):
        inspector.inspect_existing_snapshots(history=True)
    assert len(requests) == 1


def test_cloud_inspection_matches_the_single_deployed_runtime_selector(monkeypatch):
    install_cloud_fakes(monkeypatch, {"items": [archive()]}, runtime_target=True)
    result = inspector.inspect_existing_snapshots(history=True)
    row = result["snapshots"][0]
    assert row["configured_account_available"] is True
    assert row["configured_account_matches"] is True


def test_history_failure_never_echoes_paths_or_body(monkeypatch, capsys):
    def fail(**kwargs):
        raise RuntimeError("PRIVATE_ACCOUNT PRIVATE_SECRET 9876.543")
    monkeypatch.setattr(inspector, "inspect_existing_snapshots", fail)
    assert main(history=True) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "inspection_unavailable"}


def test_configured_selector_uses_runtime_target_and_exact_single_list():
    target = json.dumps({"account_selector": [ACCOUNT]})
    assert inspector.configured_account({"RUNTIME_TARGET_JSON": target}) == ACCOUNT
    assert inspector.configured_account({"FIRSTRADE_ACCOUNT": ACCOUNT}) == ACCOUNT
    assert inspector.configured_account({"RUNTIME_TARGET_JSON": target, "FIRSTRADE_ACCOUNT": ACCOUNT}) == ACCOUNT
    assert inspector.configured_account({"QSL_RUNTIME_TARGET_JSON": target,
                                         "RUNTIME_TARGET_JSON": "invalid"}) == ACCOUNT
    assert inspector.configured_account({"RUNTIME_TARGET_JSON": json.dumps({"account_selector": []})}) is None
    assert inspector.configured_account({"RUNTIME_TARGET_JSON": json.dumps({"account_selector": [ACCOUNT, "other"]})}) is None
    assert inspector.configured_account({"QSL_RUNTIME_TARGET_JSON": None,
                                         "FIRSTRADE_ACCOUNT": ACCOUNT}) is None


@pytest.mark.parametrize("target", ["invalid", "[]", '{"account_selector":{},"private":"PRIVATE"}',
                                    '{"account_selector":"one","account_selector":"two"}'])
def test_invalid_runtime_target_never_falls_back_to_direct_account(target):
    with pytest.raises(ValueError):
        inspector.configured_account({"RUNTIME_TARGET_JSON": target, "FIRSTRADE_ACCOUNT": ACCOUNT})


def test_conflicting_direct_and_runtime_selectors_are_not_qualified():
    with pytest.raises(ValueError, match="mismatch"):
        inspector.configured_account({"RUNTIME_TARGET_JSON": json.dumps({"account_selector": ["other"]}),
                                      "FIRSTRADE_ACCOUNT": ACCOUNT})


def test_fixed_provider_envelope_labels_are_not_fuzzy_cash_matches():
    data = snapshot()
    data["balance_metrics"] = {"result.cash_balance": 123.45, "result.total_value": 234.56,
                               "result.PRIVATE_cash_balance": 345.67}
    result = inspector.project_history(data, account=NAMESPACE, archive_date="2026-01-02",
                                       configured_account=ACCOUNT)
    assert result["known_metric_types"] == {"result.cash_balance": "number", "result.total_value": "number"}
    assert result["unlisted_metric_count"] == 1
    assert all(v not in json.dumps(result) for v in ["PRIVATE", "123.45", "234.56", "345.67"])


def test_report_schema_never_exports_values_dynamic_keys_or_qualifies_submissions():
    result = inspector.project_report_schema({
        "account": ACCOUNT, "dry_run": True, "started_at": "2026-01-02T03:04:05Z",
        "PRIVATE_DYNAMIC_KEY": "PRIVATE_TOKEN",
        "summary": {"orders_previewed": [{"symbol": "PRIVATE_SYMBOL", "quantity": 9876.543,
            "status": "submitted", "order_id": "PRIVATE_ORDER", "PRIVATE_KEY": "PRIVATE_VALUE"}]},
        "diagnostics": {"cash_balance_change": 9876.543},
    }, ACCOUNT)
    assert result["configured_account_matches"] is True
    assert result["observation_date"] == "2026-01-02"
    assert result["dry_run"] is True
    assert result["qualified_execution_count"] is None
    assert result["containers"] == [{"path": "$.summary.orders_previewed", "container_type": "list", "row_count": 1,
        "non_object_row_count": 0,
        "known_field_types": {"order_id": ["str"], "symbol": ["str"], "quantity": ["float"], "status": ["str"]},
        "unlisted_field_count": 1}]
    assert all(x not in json.dumps(result) for x in (ACCOUNT, "PRIVATE", "9876", "submitted", "cash_balance_change"))


def test_report_unknown_identity_and_missing_rows_are_not_true_empty_executions():
    result = inspector.project_report_schema({"account_alias": ACCOUNT, "timestamp": "PRIVATE_TOKEN"}, ACCOUNT)
    assert result["configured_account_matches"] is None
    assert result["native_account_present"] is False
    assert result["observation_date"] is None
    assert result["containers"] == []
    assert result["qualified_execution_count"] is None
    result = inspector.project_report_schema({"account_id": "other", "fills": []}, ACCOUNT)
    assert result["configured_account_matches"] is False
    assert result["containers"][0]["row_count"] == 0
    assert result["qualified_execution_count"] is None
    for fields in ({"account": ACCOUNT, "account_id": "other"}, {"account": "****1234"}):
        result = inspector.project_report_schema(fields, ACCOUNT)
        assert result["configured_account_matches"] is None


@pytest.mark.parametrize("selector,expected", [(ACCOUNT, True), ([ACCOUNT], True), ("other", False),
                                            ([ACCOUNT, "other"], None), (None, None)])
def test_report_runtime_selector_is_compared_without_inventing_native_identity(selector, expected):
    result = inspector.project_report_schema({"runtime_target": {"account_selector": selector}}, ACCOUNT)
    assert result["runtime_selector_matches"] is expected
    assert result["native_account_present"] is False
    assert result["configured_account_matches"] is None
    assert result["qualified_execution_count"] is None
    assert ACCOUNT not in json.dumps(result)


def test_unknown_report_container_is_not_an_empty_or_qualified_execution_list():
    result = inspector.project_report_schema({"orders": {"PRIVATE": "PRIVATE_TOKEN"},
        "trades": ["PRIVATE_ORDER"]}, ACCOUNT)
    assert result["containers"][0]["row_count"] is None
    assert result["containers"][0]["container_type"] == "dict"
    assert result["containers"][1]["row_count"] == 1
    assert result["containers"][1]["non_object_row_count"] == 1
    assert result["qualified_execution_count"] is None
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("uri", ["gs://private-bucket", "gs://private-bucket/", "https://private-bucket/reports",
                               "gs://private-bucket/reports?PRIVATE_TOKEN", "gs://private-bucket/../reports",
                               "gs://private-bucket/reports//other", "gs://private-bucket/reports\n"])
def test_report_location_does_not_expand_to_bucket_or_external_destinations(uri):
    with pytest.raises(ValueError):
        inspector.report_location(uri)


def test_report_transport_uses_only_existing_prefix_and_pinned_generation(monkeypatch):
    listing = {"items": [{"name": "private-reports/2026-01/report.json", "generation": "123"}]}
    requests = install_cloud_fakes(monkeypatch, listing, reports=True)
    result = inspector.inspect_existing_snapshots(reports=True)
    assert len(requests) == 2
    assert result["report_count"] == 1
    assert result["reports"][0]["configured_account_matches"] is True
    assert result["broker_requests"] == result["mutations"] == 0
    assert all(x not in json.dumps(result) for x in (ACCOUNT, "private-reports", "PRIVATE_TOKEN"))


@pytest.mark.parametrize("listing", [
    {"items": [], "nextPageToken": "PRIVATE"},
    {"items": [{"name": "other/report.json", "generation": "123"}]},
    {"items": [{"name": "private-reports/report.json", "generation": "PRIVATE_TOKEN"}]},
    {"items": [{"name": f"private-reports/{i}.json", "generation": "123"} for i in range(65)]},
])
def test_report_listing_failure_reads_no_private_body(monkeypatch, listing):
    requests = install_cloud_fakes(monkeypatch, listing, reports=True)
    with pytest.raises(ValueError):
        inspector.inspect_existing_snapshots(reports=True)
    assert len(requests) == 1


def test_report_failure_does_not_echo_private_exception(monkeypatch, capsys):
    def fail(**kwargs):
        raise RuntimeError("PRIVATE_TOKEN PRIVATE_ACCOUNT 9876.543")
    monkeypatch.setattr(inspector, "inspect_existing_snapshots", fail)
    assert main(reports=True) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "inspection_unavailable"}
