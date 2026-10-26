import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from application.account_facts_publisher import (
    AccountFactsPublishConfig, AccountFactsPublishError, publish_firstrade_account_snapshot,
)
from application.account_facts_readonly import AccountFactsUnavailable, _decimal_text
from scripts import publish_archived_account_history as history


ACCOUNT = "synthetic-account"
DAY = (datetime.now(timezone.utc).date() - timedelta(days=3)).isoformat()
STAMP = DAY.replace("-", "") + "T030405Z"
PREFIX = "state/accounts/"
SOURCE = hashlib.sha256(json.dumps({
    "platform": "firstrade", "service_name": "synthetic-service",
    "deployment_selector": "synthetic-deployment", "account_scope": "live",
    "broker_account_id": ACCOUNT,
}, separators=(",", ":")).encode()).hexdigest()


def config():
    return AccountFactsPublishConfig(
        "https://qsl-strategy-switch-console.pigbibi.workers.dev/api/account-facts/sync",
        "synthetic-token", "synthetic-target", SOURCE, "synthetic-key", "live", ACCOUNT,
        position_currency="USD",
    )


def snapshot():
    return {"account": ACCOUNT, "as_of": DAY + "T03:04:05+00:00", "balance_metrics": {
        "account_list_total_value": Decimal("100.12345678"),
        "result.cash_balance": Decimal("-12.34567890"),
        "result.cash_balance_change": 99, "result.margin_balance": 999,
    }, "positions": [{"symbol": "SYNTH", "quantity": 4}]}


def archive():
    namespace = ACCOUNT.replace("-", "_")
    return {"name": f"{PREFIX}{namespace}/funds/history/{DAY.replace('-', '/')}/{STAMP}.json",
            "generation": "321"}


def test_archive_preserves_exact_values_and_original_time_without_guessing_positions_or_flows():
    payload = history.archive_payload(snapshot(), archive_date=DAY, config=config())
    assert payload["cash"][0]["cash_balance"] == "-12.3456789"
    assert payload["broker_reported_balances"][0]["net_assets"] == "100.12345678"
    assert payload["observed_finished_at"] == DAY + "T03:04:05+00:00"
    assert payload["observation_date"] == DAY
    assert "broker_reported_positions" not in payload
    assert "externalFlow" not in payload
    assert "return" not in payload


@pytest.mark.parametrize("change", ["identity", "date", "time_zone", "cash", "equity", "conflicting_cash"])
def test_rejects_unproven_archive_instead_of_filling_defaults(change):
    data = snapshot()
    if change == "identity": data["account"] = "another-account"
    elif change == "date": data["as_of"] = "2000-01-01T03:04:05Z"
    elif change == "time_zone": data["as_of"] = DAY + "T03:04:05"
    elif change == "cash": del data["balance_metrics"]["result.cash_balance"]
    elif change == "equity": del data["balance_metrics"]["account_list_total_value"]
    else: data["balance_metrics"]["cash_balance"] = 100
    with pytest.raises((ValueError, RuntimeError)):
        history.archive_payload(data, archive_date=DAY, config=config())


def test_archive_selection_does_not_mix_accounts_or_accept_duplicates_or_incomplete_listing():
    obj = archive()
    other = {**obj, "name": obj["name"].replace("synthetic_account", "other_account")}
    assert history.selected_archive({"items": [other, obj]}, PREFIX, account=ACCOUNT, archive_date=DAY) == obj
    for listing in [{"items": [other]}, {"items": [obj, obj]}, {"items": [obj], "nextPageToken": "more"}]:
        with pytest.raises(ValueError):
            history.selected_archive(listing, PREFIX, account=ACCOUNT, archive_date=DAY)


def test_historical_transport_requires_history_only_ack_and_uses_existing_dedicated_token():
    cfg = config()
    payload = history.archive_payload(snapshot(), archive_date=DAY, config=cfg)
    ack = {"ok": True, "stored": True, "platform": "firstrade", "account_key": cfg.account_key,
           "target_id": cfg.target_id, "observation_date": DAY,
           "observed_finished_at": payload["observed_finished_at"],
           "historical": True, "latest_unchanged": True}
    for historical_ack in [True, False]:
        calls = []
        response = SimpleNamespace(status_code=200, content=b"{}", json=lambda: {
            **ack, "latest_unchanged": historical_ack,
        })
        session = SimpleNamespace(post=lambda *a, **kw: (calls.append((a, kw)) or response), close=lambda: None)
        if historical_ack:
            publish_firstrade_account_snapshot(payload, cfg, session_factory=lambda: session, historical=True)
        else:
            with pytest.raises(AccountFactsPublishError, match="sync_ack_invalid"):
                publish_firstrade_account_snapshot(payload, cfg, session_factory=lambda: session, historical=True)
        assert len(calls) == 1
        assert calls[0][0][0].endswith("/api/account-facts/history/sync")
        assert calls[0][1]["headers"] == {"Authorization": "Bearer synthetic-token"}
        assert calls[0][1]["allow_redirects"] is False


def install_fakes(monkeypatch, *, mismatched_source=False, wrong_account=False,
                  secret_reference=False, deny_secret=False, commands=None):
    cfg = config()
    env = {
        "GCP_PROJECT_ID": "synthetic-project", "CLOUD_RUN_REGION": "synthetic-region",
        "CLOUD_RUN_SERVICE": "synthetic-service", "FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED": "true",
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_URL": cfg.sync_url,
        "FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN": cfg.sync_token,
        "FIRSTRADE_ACCOUNT_FACTS_TARGET_ID": cfg.target_id,
        "FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID": "b" * 64 if mismatched_source else SOURCE,
        "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_KEY": cfg.account_key,
        "FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE": cfg.account_scope,
    }
    target = {"account_selector": [ACCOUNT], "service_name": "synthetic-service",
              "deployment_selector": "synthetic-deployment", "account_scope": "live"}
    runtime = {"RUNTIME_TARGET_JSON": json.dumps(target), "FIRSTRADE_GCS_STATE_BUCKET": "synthetic-bucket",
               "FIRSTRADE_STATE_PREFIX": "state", "FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN": "synthetic-token"}
    def run(args, **kw):
        if commands is not None: commands.append(args)
        if args[1:3] == ["auth", "print-access-token"]:
            return SimpleNamespace(returncode=0, stdout="synthetic-cloud-token")
        if args[1:4] == ["secrets", "versions", "access"]:
            return SimpleNamespace(returncode=int(deny_secret), stdout="synthetic-token")
        entries = [{"name": k, "value": v} for k, v in runtime.items()
                   if not (secret_reference and k == "FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN")]
        if secret_reference:
            entries.append({"name": "FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN", "valueFrom": {
                "secretKeyRef": {"name": "synthetic-sync-secret", "key": "latest"},
            }})
        value = ({"status": {"traffic": [{"percent": 100, "revisionName": "synthetic-revision"}]}}
                 if "services" in args else
                 {"spec": {"containers": [{"env": entries}]}})
        return SimpleNamespace(returncode=0, stdout=json.dumps(value))
    monkeypatch.setattr(history.subprocess, "run", run)
    reads = []
    class Response:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self, size): return self.body
    def urlopen(request, **kw):
        reads.append(request.full_url)
        if parse_qs(urlsplit(request.full_url).query).get("alt") == ["media"]:
            data = copy.deepcopy(snapshot())
            if wrong_account: data["account"] = "other-account"
            return Response(json.dumps(data, default=str).encode())
        return Response(json.dumps({"items": [archive()]}).encode())
    monkeypatch.setattr(history.urllib.request, "urlopen", urlopen)
    published = []
    monkeypatch.setattr(history, "publish_firstrade_account_snapshot", lambda *a, **kw: published.append((a, kw)))
    return env, reads, published


def test_preview_reads_one_fixed_generation_and_never_publishes(monkeypatch):
    env, reads, published = install_fakes(monkeypatch)
    del env["FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN"]
    result = history.execute(DAY, env=env)
    assert result["status"] == "preview" and result["publish_requests"] == 0
    assert not published and len(reads) == 2
    assert parse_qs(urlsplit(reads[1]).query) == {"alt": ["media"], "generation": ["321"]}
    assert ACCOUNT not in json.dumps(result) and "100.123" not in json.dumps(result)


def test_publish_uses_history_ingress_after_identity_validation(monkeypatch):
    env, _, published = install_fakes(monkeypatch)
    assert history.execute(DAY, publish=True, env=env)["status"] == "published"
    assert len(published) == 1 and published[0][1] == {"historical": True}


@pytest.mark.parametrize("failure", ["source", "identity"])
def test_failed_source_or_native_identity_causes_zero_posts(monkeypatch, failure):
    env, reads, published = install_fakes(monkeypatch, mismatched_source=failure == "source", wrong_account=failure == "identity")
    with pytest.raises(ValueError): history.execute(DAY, publish=True, env=env)
    assert published == []
    if failure == "source": assert reads == []


def test_secret_payload_is_not_read_for_preview_but_publish_consumes_existing_exact_reference(monkeypatch):
    commands = []
    env, _, published = install_fakes(monkeypatch, secret_reference=True, commands=commands)
    history.execute(DAY, env=env)
    assert not any("access" in args for args in commands) and not published
    history.execute(DAY, publish=True, env=env)
    access = [args for args in commands if "access" in args]
    assert len(access) == 1
    assert access[0][1:7] == ["secrets", "versions", "access", "latest", "--secret", "synthetic-sync-secret"]
    assert published[0][0][1].sync_token == "synthetic-token"


def test_secret_access_denial_stops_without_post_or_retry(monkeypatch):
    commands = []
    env, _, published = install_fakes(monkeypatch, secret_reference=True, deny_secret=True, commands=commands)
    with pytest.raises(ValueError, match="dedicated_sync_token_unavailable"):
        history.execute(DAY, publish=True, env=env)
    assert not published and sum("access" in args for args in commands) == 1


def test_cli_never_prints_unexpected_error_values(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["history", "--date", DAY])
    def fail(*args, **kwargs): raise ValueError("PRIVATE_TOKEN_AND_ACCOUNT")
    monkeypatch.setattr(history, "execute", fail)
    assert history.main() == 1
    assert json.loads(capsys.readouterr().out) == {"status": "archive_publish_unavailable", "reason": "unavailable"}


@pytest.mark.parametrize("numeric,expected", [(0.00000001, "0.00000001"), (-0.00000001, "-0.00000001")])
def test_json_numeric_small_cash_roundtrip_keeps_exact_supported_precision(numeric, expected):
    data = snapshot()
    data["balance_metrics"]["result.cash_balance"] = json.loads(json.dumps(numeric), parse_float=Decimal)
    payload = history.archive_payload(data, archive_date=DAY, config=config())
    assert payload["cash"][0]["cash_balance"] == expected


@pytest.mark.parametrize("value", ["1E-1000000", "1E1000000", "NaN", "Infinity", "-Infinity"])
def test_decimal_exponent_expansion_is_bounded_before_formatting(value):
    with pytest.raises(AccountFactsUnavailable):
        _decimal_text(Decimal(value))


@pytest.mark.parametrize("code", ["SYNTHETIC_ACCOUNT_MARKER", {"private": "SYNTHETIC_ACCOUNT_MARKER"}])
def test_rejected_http_error_cannot_echo_private_identity(code, capsys):
    cfg = config()
    response = SimpleNamespace(status_code=400, json=lambda: {"error": code})
    session = SimpleNamespace(post=lambda *args, **kwargs: response, close=lambda: None)
    with pytest.raises(AccountFactsPublishError, match="sync_rejected"):
        publish_firstrade_account_snapshot(
            history.archive_payload(snapshot(), archive_date=DAY, config=cfg),
            cfg, session_factory=lambda: session, historical=True,
        )
    log = capsys.readouterr().out
    assert "SYNTHETIC_ACCOUNT_MARKER" not in log and "status=400" in log


def test_known_static_qrs_conflict_remains_diagnosable(capsys):
    cfg = config()
    response = SimpleNamespace(status_code=409, json=lambda: {"error": "account_facts_daily_conflict"})
    session = SimpleNamespace(post=lambda *args, **kwargs: response, close=lambda: None)
    with pytest.raises(AccountFactsPublishError, match="sync_rejected"):
        publish_firstrade_account_snapshot(
            history.archive_payload(snapshot(), archive_date=DAY, config=cfg),
            cfg, session_factory=lambda: session, historical=True,
        )
    assert "error=account_facts_daily_conflict" in capsys.readouterr().out
