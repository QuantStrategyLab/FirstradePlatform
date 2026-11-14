from datetime import datetime, timezone
import json

import pytest

from scripts import publish_runtime_daily as daily
from notifications.digest_snapshot import digest_archive as archive


def test_empty_listing_has_measured_zero_counts(monkeypatch):
    monkeypatch.setattr(daily.heartbeat, '_report_globs', lambda *_: ['fake'])
    monkeypatch.setattr(daily.heartbeat, '_list_gcs_objects', lambda *_, **__: [])
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    result = daily._read_reports(now.date(), now, None)
    assert result.diagnostics == {'files_scanned': 0, 'files_eligible': 0, 'files_read': 0,
                                  'archive_outcome': 'success'}


def test_failed_listing_keeps_counts_unknown(monkeypatch):
    monkeypatch.setattr(daily.heartbeat, '_report_globs', lambda *_: ['fake'])
    def fail(*_, **__):
        raise RuntimeError('fake-private-provider-message')
    monkeypatch.setattr(daily.heartbeat, '_list_gcs_objects', fail)
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    result = daily._read_reports(now.date(), now, None)
    assert result[1] == 'list_failed'
    assert result.diagnostics['files_scanned'] is None
    assert 'fake-private' not in json.dumps(result.diagnostics)


@pytest.mark.parametrize('operation', ['ls', 'cat'])
def test_archive_failure_has_fixed_operation_category(monkeypatch, operation):
    from google.cloud import storage
    from google.api_core.exceptions import Forbidden
    def fail(*_, **__):
        raise Forbidden('fake-private-provider-message')
    monkeypatch.setattr(storage, 'Client', fail)
    with pytest.raises(ValueError) as caught:
        archive._read([operation, 'gs://fake-bucket/fake-key'])
    assert str(caught.value) == 'candidate_archive_read_failed:initialize:permission_denied'
    assert caught.value.__suppress_context__


def test_cli_reports_exclusion_counts_without_private_values(monkeypatch, capsys):
    monkeypatch.setattr(daily, '_FACTS_URL', 'https://example.test/api/account-facts/sync')
    monkeypatch.setattr(daily, '_binding', lambda *_: ('fake-account', 'a' * 64, 'b' * 64))
    env = {'RUNTIME_TARGET_JSON': json.dumps({'service_name': 'fake-service',
           'strategy_profile': 'fake-profile', 'account_scope': 'paper'}),
           'EXECUTION_EVIDENCE_SYNC_TOKEN': 'fake-token',
           'EXECUTION_EVIDENCE_SYNC_URL': 'https://example.test/api/execution-evidence/sync',
           'RUNTIME_DAILY_BUSINESS_DATE': '2026-11-03'}
    base = {'platform': 'firstrade', 'service_name': 'fake-service',
            'strategy_profile': 'fake-profile', 'account_scope': 'paper',
            'account_facts_source_binding_id': 'a' * 64,
            'started_at': '2026-11-03T18:00:00Z', 'finished_at': '2026-11-03T18:01:00Z',
            'run_id': 'fake-private-run', 'errors': []}
    reports = [{}, {**base, 'started_at': 'bad'},
               {**base, 'started_at': '2026-11-02T18:00:00Z'}, {**base, 'errors': None}]
    monkeypatch.setattr(daily, '_read_reports', lambda *_: daily._ReadResult(
        reports, None, dict(files_scanned=5, files_eligible=4, files_read=4, archive_outcome='success')))
    diagnostics = {}
    assert daily.publish_once(env, now=datetime(2026, 11, 4, tzinfo=timezone.utc),
                              diagnostics=diagnostics) == 'no_attributable_runs'
    assert diagnostics['files_attributable'] == 0
    assert diagnostics['excluded_by_reason'] == dict(identity_unverified=1, invalid_report=1,
        invalid_run_time=1, other_business_date=1, duplicate_run=0)
    assert 'fake-private' not in json.dumps(diagnostics)
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize(('error', 'category'), [(TimeoutError('fake-private'), 'timeout'),
    (ValueError('fake-private'), 'unknown')])
def test_archive_read_failure_never_exposes_provider_text(monkeypatch, error, category):
    from google.cloud import storage
    monkeypatch.setattr(storage, 'Client', lambda: object())
    def fail(*_):
        raise error
    monkeypatch.setattr(archive.ArchiveReader, 'read', fail)
    with pytest.raises(ValueError) as caught:
        archive._read(['cat', 'gs://fake-bucket/fake-key'])
    assert str(caught.value) == f'candidate_archive_read_failed:read:{category}'


def test_archive_diagnostic_public_boundary_is_allowlisted():
    from scripts.emit_unified_digest_candidates import _failure_diagnostic
    assert _failure_diagnostic(ValueError('candidate_archive_read_failed:read:timeout'), 'projection') == {
        'stage': 'archive_read', 'error_code': 'candidate_archive_read_failed',
        'operation': 'read', 'error_category': 'timeout'}
    assert _failure_diagnostic(ValueError('candidate_archive_read_failed:read:fake-private'),
                               'projection') == {'stage': 'projection', 'error_code': 'unexpected_error'}
