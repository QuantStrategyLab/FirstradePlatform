"""Summarize bounded snapshot request logs without emitting their contents."""
from __future__ import annotations

import json
import os
import re
import subprocess
from urllib.parse import urlsplit

from scripts.diagnose_cached_stage_failure import _validate_window

LIMIT = 200
REASONS = frozenset({
    'cache_config_error', 'cache_read_failed', 'cache_missing', 'cache_stale',
    'cache_invalid', 'cache_tokens_incomplete', 'cache_missing_or_stale',
    'read_timeout', 'read_network_error', 'read_http_error', 'read_invalid_payload',
    'read_provider_error', 'account_data_invalid', 'account_identity_unavailable',
    'account_identity_mismatch', 'account_list_empty', 'balances_unavailable',
    'positions_unavailable', 'readonly_client_unavailable', 'sync_unavailable',
    'sync_rejected', 'sync_ack_invalid', 'configuration_incomplete',
    'account_facts_unavailable', 'runtime_target_mismatch',
})
STAGES = frozenset({'cache', 'userinfo', 'account_list', 'account_read', 'unclassified'})
STATUSES = frozenset({'OK', 'CANCELLED', 'UNKNOWN', 'INVALID_ARGUMENT', 'DEADLINE_EXCEEDED',
    'NOT_FOUND', 'ALREADY_EXISTS', 'PERMISSION_DENIED', 'RESOURCE_EXHAUSTED',
    'FAILED_PRECONDITION', 'ABORTED', 'OUT_OF_RANGE', 'UNIMPLEMENTED', 'INTERNAL',
    'UNAVAILABLE', 'DATA_LOSS', 'UNAUTHENTICATED'})


def _http(value):
    return value if type(value) is int and 100 <= value <= 599 else None


def _target(url):
    if not isinstance(url, str):
        return None
    try:
        p = urlsplit(url)
        if (p.scheme != 'https' or not p.hostname or p.username or p.password
                or p.port is not None or p.path != '/account-facts-sync' or p.query or p.fragment):
            return None
        return p.netloc
    except ValueError:
        return None


def _resource(row, kind, project, service, location):
    resource = row.get('resource') if isinstance(row, dict) else None
    labels = resource.get('labels') if isinstance(resource, dict) else None
    return (isinstance(labels, dict) and resource.get('type') == kind
            and labels.get('project_id') == project and labels.get('location') == location
            and labels.get('service_name' if kind == 'cloud_run_revision' else 'job_id')
            == (service if kind == 'cloud_run_revision' else service + '-account-facts-sync-scheduler'))


def _classification(row):
    msg = row.get('textPayload')
    if not isinstance(msg, str):
        return None
    client = re.search(r'account_facts_sync client failed exc_type=\w+ reason=([a-z_]+) stage=([a-z_]+) http_status=(None|[1-5][0-9]{2})(?:\s|$)', msg)
    other = re.search(r'account_facts_sync (?:collect|build|publish) failed reason=([a-z_]+)(?:\s|$)', msg)
    if client and client.group(1) in REASONS and client.group(2) in STAGES:
        return (client.group(1), client.group(2),
                None if client.group(3) == 'None' else int(client.group(3)))
    if other and other.group(1) in REASONS:
        return (other.group(1), 'unclassified', None)
    return None


def summarize(rows, app_rows, project, service, region, run_region=None):
    run_region = run_region or region
    job = f'projects/{project}/locations/{region}/jobs/{service}-account-facts-sync-scheduler'
    finished, requests = [], []
    for row in rows:
        payload = row.get('jsonPayload', {}) if isinstance(row, dict) else {}
        if (_resource(row, 'cloud_scheduler_job', project, service, region) and isinstance(payload, dict)
                and payload.get('@type') == 'type.googleapis.com/google.cloud.scheduler.logging.AttemptFinished'
                and payload.get('jobName') == job and _target(payload.get('url'))):
            finished.append(payload)
        http = row.get('httpRequest', {}) if isinstance(row, dict) else {}
        if (_resource(row, 'cloud_run_revision', project, service, run_region) and isinstance(http, dict)
                and http.get('requestMethod') == 'POST' and _target(http.get('requestUrl'))):
            requests.append(row)
    result = {'scheduler_count': len(finished), 'request_count': len(requests),
              'scheduler_status': 'unknown', 'scheduler_http_status': None,
              'request_http_status': None, 'association': 'unverified',
              'scheduler_request_relation': 'unknown', 'app_reason': 'unknown',
              'app_stage': 'unknown', 'app_http_status': None, 'app_match_count': 0}
    if len(finished) == 1:
        event = finished[0]
        status = event.get('status')
        result['scheduler_status'] = status if status in STATUSES else 'unknown'
        debug = event.get('debugInfo')
        match = re.search(r'\bresponse code number = ([1-5][0-9]{2})\b', debug) if isinstance(debug, str) else None
        result['scheduler_http_status'] = int(match.group(1)) if match else None
    if len(requests) == 1:
        result['request_http_status'] = _http(requests[0]['httpRequest'].get('status'))
    if len(finished) != 1 or len(requests) != 1:
        result['association'] = 'ambiguous' if len(finished) > 1 or len(requests) > 1 else 'unverified'
        return result
    if _target(finished[0].get('url')) != _target(requests[0]['httpRequest'].get('requestUrl')):
        return result
    result['scheduler_request_relation'] = 'same_target_and_window_only'
    trace = requests[0].get('trace')
    if not isinstance(trace, str) or not re.fullmatch(r'projects/' + re.escape(project) + r'/traces/[0-9a-f]{32}', trace):
        return result
    result['association'] = 'unique_trace'
    matched = [row for row in app_rows if _resource(row, 'cloud_run_revision', project, service, run_region)
               and row.get('trace') == trace and not row.get('httpRequest')]
    result['app_match_count'] = len(matched)
    classifications = [value for row in matched if (value := _classification(row)) is not None]
    if len(matched) == 1 and len(classifications) == 1:
        result['app_reason'], result['app_stage'], result['app_http_status'] = classifications[0]
    return result


def _read_logs(query, project):
    try:
        proc = subprocess.run(['gcloud', 'logging', 'read', query, '--project', project,
                               '--limit', str(LIMIT + 1), '--format=json', '--quiet'],
                              capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return 'unavailable', []
    if proc.returncode:
        return ('permission_denied' if re.search(r'permission.denied|forbidden|\b403\b', proc.stderr, re.I)
                else 'unavailable'), []
    try:
        rows = json.loads(proc.stdout)
        if not isinstance(rows, list):
            return 'unavailable', []
    except (ValueError, TypeError):
        return 'unavailable', []
    return ('truncated' if len(rows) > LIMIT else 'ok'), rows[:LIMIT]


def run(project, service, region, run_region, start, end, *, read=_read_logs):
    _validate_window(start, end)
    if (not re.fullmatch(r'[a-z][a-z0-9-]{4,62}', project)
            or not re.fullmatch(r'[a-z][a-z0-9-]{0,62}', service)
            or not re.fullmatch(r'[a-z]+(?:-[a-z0-9]+)+[0-9]', region)
            or not re.fullmatch(r'[a-z]+(?:-[a-z0-9]+)+[0-9]', run_region)):
        raise ValueError('target_invalid')
    window = f'timestamp >= "{start}" AND timestamp <= "{end}"'
    job_id = service + '-account-facts-sync-scheduler'
    route = r'^https://[^/?#]+/account-facts-sync$'
    scope = (f'{window} AND resource.labels.project_id="{project}" AND ('
             f'(resource.type="cloud_scheduler_job" AND resource.labels.location="{region}" '
             f'AND resource.labels.job_id="{job_id}" AND jsonPayload."@type"="type.googleapis.com/google.cloud.scheduler.logging.AttemptFinished") OR '
             f'(resource.type="cloud_run_revision" AND resource.labels.location="{run_region}" '
             f'AND resource.labels.service_name="{service}" AND httpRequest.requestMethod="POST" '
             f'AND httpRequest.requestUrl=~"{route}"))')
    status, rows = read(scope, project)
    output = {'status': status, 'app_read_status': 'not_attempted'}
    if status != 'ok':
        return output
    partial = summarize(rows, [], project, service, region, run_region)
    if partial['association'] != 'unique_trace':
        return {**output, **partial}
    request = next(row for row in rows if _resource(row, 'cloud_run_revision', project, service, run_region)
                   and (row.get('httpRequest') or {}).get('requestMethod') == 'POST'
                   and _target((row.get('httpRequest') or {}).get('requestUrl')))
    trace = request['trace']
    app_filter = (f'{window} AND resource.type="cloud_run_revision" '
                  f'AND resource.labels.project_id="{project}" AND resource.labels.location="{run_region}" '
                  f'AND resource.labels.service_name="{service}" AND trace="{trace}"')
    app_status, app_rows = read(app_filter, project)
    output['app_read_status'] = app_status
    if app_status != 'ok':
        return {**output, **partial}
    result = {**output, **summarize(rows, app_rows, project, service, region, run_region)}
    if result['app_match_count']:
        return result
    candidate_filter = (f'{window} AND resource.type="cloud_run_revision" '
                        f'AND resource.labels.project_id="{project}" AND resource.labels.location="{run_region}" '
                        f'AND resource.labels.service_name="{service}" AND textPayload:"account_facts_sync"')
    candidate_status, candidates = read(candidate_filter, project)
    result.update(candidate_read_status=candidate_status, candidate_count=None,
                  candidate_relation='unknown', candidate_reason='unknown',
                  candidate_stage='unknown', candidate_http_status=None)
    if candidate_status != 'ok':
        return result
    candidates = [row for row in candidates if _resource(row, 'cloud_run_revision', project, service, run_region)
                  and not row.get('httpRequest') and isinstance(row.get('textPayload'), str)
                  and 'account_facts_sync' in row['textPayload']]
    result['candidate_count'] = len(candidates)
    if len(candidates) == 1:
        result['candidate_relation'] = 'same_service_and_window_only'
        classification = _classification(candidates[0])
        if classification is not None:
            result['candidate_reason'], result['candidate_stage'], result['candidate_http_status'] = classification
    elif len(candidates) > 1:
        result['candidate_relation'] = 'ambiguous'
    return result


def main():
    try:
        output = run(os.environ['GCP_PROJECT_ID'], os.environ['CLOUD_RUN_SERVICE'],
                     os.environ['SCHEDULER_LOCATION'], os.environ['CLOUD_RUN_REGION'], os.environ['FAILURE_WINDOW_START'],
                     os.environ['FAILURE_WINDOW_END'])
    except (KeyError, ValueError, TypeError):
        output = {'status': 'invalid_configuration'}
    except Exception:
        output = {'status': 'unavailable'}
    print(json.dumps(output, sort_keys=True))
    failures = {'permission_denied', 'unavailable', 'truncated'}
    return 0 if output['status'] == 'ok' and output.get('app_read_status') not in failures and output.get('candidate_read_status') not in failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
