import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.diagnose_snapshot_request_logs import _read_logs, run, summarize

PROJECT = 'example-project'
SERVICE = 'sample-service'
JOB = f'projects/{PROJECT}/locations/us-central1/jobs/{SERVICE}-account-facts-sync-scheduler'
URL = 'https://example.invalid/account-facts-sync'
TRACE = f'projects/{PROJECT}/traces/' + 'a' * 32


def scheduler():
    return {'resource': {'type': 'cloud_scheduler_job', 'labels': {'project_id': PROJECT, 'location': 'us-central1', 'job_id': SERVICE + '-account-facts-sync-scheduler'}}, 'jsonPayload': {'@type': 'type.googleapis.com/google.cloud.scheduler.logging.AttemptFinished', 'jobName': JOB, 'url': URL, 'status': 'UNAVAILABLE', 'debugInfo': 'Original HTTP response code number = 503'}}


def request():
    return {'resource': {'type': 'cloud_run_revision', 'labels': {'project_id': PROJECT, 'location': 'us-central1', 'service_name': SERVICE}}, 'httpRequest': {'requestUrl': URL, 'requestMethod': 'POST', 'status': 503}, 'trace': TRACE}


def app(reason='cache_missing', trace=TRACE):
    return {'resource': {'type': 'cloud_run_revision', 'labels': {'project_id': PROJECT, 'location': 'us-central1', 'service_name': SERVICE}}, 'trace': trace, 'textPayload': 'account_facts_sync client failed exc_type=FirstradeCachedSessionError reason=' + reason + ' stage=cache http_status=None'}


class SnapshotLogDiagnosticTest(unittest.TestCase):
    def test_unique_trace_and_safe_category(self):
        result = summarize([scheduler(), request()], [app()], PROJECT, SERVICE, 'us-central1')
        self.assertEqual(result['association'], 'unique_trace')
        self.assertEqual(result['app_reason'], 'cache_missing')
        self.assertEqual(result['app_stage'], 'cache')
        self.assertEqual(result['request_http_status'], 503)
        self.assertNotIn(URL, str(result))
        self.assertNotIn(TRACE, str(result))

    def test_no_trace_or_multiple_requests_are_never_correlated(self):
        no_trace = request(); del no_trace['trace']
        for rows in ([scheduler(), no_trace], [scheduler(), request(), request()]):
            result = summarize(rows, [app()], PROJECT, SERVICE, 'us-central1')
            self.assertNotEqual(result['association'], 'unique_trace')
            self.assertEqual(result['app_reason'], 'unknown')

    def test_malicious_or_unmatched_payload_never_leaks(self):
        for row in (app('secret-account-123'), app(trace='unrelated')):
            result = summarize([scheduler(), request()], [row], PROJECT, SERVICE, 'us-central1')
            self.assertEqual(result['app_reason'], 'unknown')
            self.assertNotIn('secret-account-123', str(result))

    def test_wrong_job_or_url_cannot_match(self):
        wrong = scheduler(); wrong['jsonPayload']['jobName'] += '-wrong'
        result = summarize([wrong, request()], [app()], PROJECT, SERVICE, 'us-central1')
        self.assertEqual(result['scheduler_count'], 0)
        self.assertEqual(result['association'], 'unverified')
        self.assertEqual(result['app_reason'], 'unknown')

    def test_actual_read_path_and_incomplete_reads(self):
        start, end = '2026-10-30T01:57:00Z', '2026-10-30T02:10:00Z'
        queries = []
        def read(query, project):
            queries.append(query)
            return ('ok', [scheduler(), request()]) if len(queries) == 1 else ('ok', [app()])
        result = run(PROJECT, SERVICE, 'us-central1', 'us-central1', start, end, read=read)
        self.assertEqual(result['app_reason'], 'cache_missing')
        self.assertEqual(len(queries), 2)
        self.assertIn('jsonPayload."@type"=', queries[0])
        self.assertIn('httpRequest.requestUrl=~', queries[0])
        self.assertIn('trace=', queries[1])
        for failure in ('permission_denied', 'truncated', 'unavailable'):
            def failed_read(query, project):
                return failure, []
            result = run(PROJECT, SERVICE, 'us-central1', 'us-central1', start, end, read=failed_read)
            self.assertEqual(result['status'], failure)
            self.assertNotIn('app_reason', result)

    def test_app_read_truncation_never_claims_reason(self):
        calls = []
        def read(query, project):
            calls.append(query)
            return ('ok', [scheduler(), request()]) if len(calls) == 1 else ('truncated', [app()])
        result = run(PROJECT, SERVICE, 'us-central1', 'us-central1',
                     '2026-10-30T01:57:00Z', '2026-10-30T02:10:00Z', read=read)
        self.assertEqual(result['app_read_status'], 'truncated')
        self.assertEqual(result['app_reason'], 'unknown')

    def test_app_classification_with_wrong_trace_or_multiple_events(self):
        for second in (app(), app('unrecognized'), {k: v for k, v in app().items() if k != 'textPayload'}):
            result = summarize([scheduler(), request()], [app(), second], PROJECT, SERVICE, 'us-central1')
            self.assertEqual(result['app_reason'], 'unknown')
            self.assertEqual(result['app_match_count'], 2)

    def test_gcloud_output_is_bounded_and_errors_are_sanitized(self):
        for completed, expected in (
            (SimpleNamespace(returncode=1, stdout='', stderr='PERMISSION_DENIED sensitive-value'), 'permission_denied'),
            (SimpleNamespace(returncode=1, stdout='', stderr='other sensitive-value'), 'unavailable'),
            (SimpleNamespace(returncode=0, stdout='[' + ','.join('{}' for _ in range(201)) + ']', stderr=''), 'truncated'),
        ):
            with patch('scripts.diagnose_snapshot_request_logs.subprocess.run', return_value=completed) as call:
                status, rows = _read_logs('synthetic filter', PROJECT)
                self.assertEqual(status, expected)
                self.assertLessEqual(len(rows), 200)
                self.assertNotIn('sensitive-value', str((status, rows)))
                self.assertEqual(call.call_args.args[0][:3], ['gcloud', 'logging', 'read'])

    def test_invalid_window_never_reads(self):
        def forbidden(*_args):
            raise AssertionError('read attempted')
        with self.assertRaises(ValueError):
            run(PROJECT, SERVICE, 'us-central1', 'us-central1',
                '2026-10-30T01:00:00Z', '2026-10-30T02:00:01Z', read=forbidden)

    def test_untraced_app_log_is_only_an_unassociated_candidate(self):
        calls = []
        untraced = app(); del untraced['trace']
        def read(query, project):
            calls.append(query)
            return ('ok', [[scheduler(), request()], [], [untraced]][len(calls) - 1])
        result = run(PROJECT, SERVICE, 'us-central1', 'us-central1',
                     '2026-10-30T01:57:00Z', '2026-10-30T02:10:00Z', read=read)
        self.assertEqual(result['app_reason'], 'unknown')
        self.assertEqual(result['candidate_relation'], 'same_service_and_window_only')
        self.assertEqual(result['candidate_reason'], 'cache_missing')
        self.assertEqual(result['candidate_count'], 1)
        self.assertIn('textPayload', calls[2])

    def test_multiple_or_truncated_candidates_never_classified(self):
        for status, rows in [('ok', [app(), app()]), ('truncated', [app()])]:
            calls = []
            def read(query, project):
                calls.append(query)
                return [('ok', [scheduler(), request()]), ('ok', []), (status, rows)][len(calls) - 1]
            result = run(PROJECT, SERVICE, 'us-central1', 'us-central1',
                         '2026-10-30T01:57:00Z', '2026-10-30T02:10:00Z', read=read)
            self.assertEqual(result['candidate_reason'], 'unknown')

    def test_unrecognized_candidate_never_exposes_payload(self):
        calls = []
        unsafe = app('secret-account-123'); del unsafe['trace']
        def read(query, project):
            calls.append(query)
            return ('ok', [[scheduler(), request()], [], [unsafe]][len(calls) - 1])
        result = run(PROJECT, SERVICE, 'us-central1', 'us-central1',
                     '2026-10-30T01:57:00Z', '2026-10-30T02:10:00Z', read=read)
        self.assertEqual(result['candidate_reason'], 'unknown')
        self.assertNotIn('secret-account-123', str(result))


if __name__ == '__main__':
    unittest.main()
