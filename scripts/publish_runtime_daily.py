"""Read the existing protected report source and send one daily projection."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from scripts import execution_report_heartbeat as heartbeat
from scripts.project_runtime_daily import PLATFORM, project_runtime_daily
from application.account_facts_publisher import _APPROVED_SYNC_URL as _FACTS_URL


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _binding(env, target):
    binding = env.get("FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID") or ""
    account = env.get("FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_KEY") or ""
    if (target.get("account_scope") != env.get("FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE")
            or not account or not binding):
        raise ValueError("daily_binding_invalid")
    return account, binding, binding


def _read_reports(day, now, project):
    """Use the existing report reader; never expose a URI or report body."""
    start = datetime.combine(day, time.min, tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
    end = min(start + timedelta(days=2), now)
    try:
        entries = []
        for glob in heartbeat._report_globs(start, end):
            entries.extend(heartbeat._list_gcs_objects(glob, project=project))
        eligible = [entry for entry in entries if heartbeat._object_updated_at(entry) is not None
                    and start - timedelta(days=1) <= heartbeat._object_updated_at(entry) <= end + timedelta(days=1)]
        if len(eligible) > 20:
            return [], "read_truncated"
        reports = []
        failed = False
        for entry in eligible:
            uri = heartbeat._object_uri(entry)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                report = heartbeat._cat_gcs_json(uri, project=project)
            if report is None:
                failed = True
            else:
                reports.append(report)
        return reports, "report_read_error" if failed else None
    except Exception:
        return [], "list_failed"


def _auth_config(env):
    token = env.get("EXECUTION_EVIDENCE_SYNC_TOKEN")
    url = urlsplit(env.get("EXECUTION_EVIDENCE_SYNC_URL") or "")
    if (not token or token != token.strip() or url.scheme != "https" or not url.hostname
            or url.username or url.password or url.query or url.fragment):
        raise ValueError("daily_config_invalid")
    if url.netloc != urlsplit(_FACTS_URL).netloc:
        raise ValueError("daily_origin_invalid")
    return token, f"https://{url.netloc}"


def auth_preflight(env, *, opener=None):
    """Use the receiver's fixed invalid-JSON branch; it performs no data write."""
    token, origin = _auth_config(env)
    request = Request(origin + "/api/runtime-daily/sync", method="POST", data=b"{", headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json",
        "User-Agent": "QuantStrategyLab-RuntimeDaily/1.0",
    })
    try:
        with (opener or build_opener(_NoRedirect())).open(request, timeout=20):
            return "unavailable"
    except HTTPError as error:
        try:
            body = error.read(4097)
            if error.code == 400 and len(body) <= 4096 and json.loads(body) == {
                "ok": False, "error": "request body must be valid JSON",
            }:
                return "verified"
        except Exception:
            pass
        return "unavailable"
    except Exception:
        return "unavailable"


def publish_once(env, *, now=None, opener=None):
    """Make one POST; an unconfirmed response is never retried."""
    now = now or datetime.now(timezone.utc)
    target = json.loads(env.get("RUNTIME_TARGET_JSON") or "null")
    if not isinstance(target, dict):
        raise ValueError("daily_config_invalid")
    service, profile, scope = (target.get(key) for key in ("service_name", "strategy_profile", "account_scope"))
    if not all(isinstance(v, str) and v for v in (service, profile, scope)):
        raise ValueError("daily_config_invalid")
    target_key = "|".join((service, profile, scope))
    account_key, account_binding, source_binding = _binding(env, target)
    token, origin = _auth_config(env)
    requested_day = env.get("RUNTIME_DAILY_BUSINESS_DATE") or ""
    day = date.fromisoformat(requested_day)
    if day.isoformat() != requested_day:
        raise ValueError("daily_date_invalid")
    # Validate before reading the protected archive.
    kwargs = dict(target_key=target_key, account_binding=account_binding,
                  business_date=day.isoformat(), observed_at=now)
    project_runtime_daily([], **kwargs)
    reports, error = _read_reports(day, now, env.get("GCP_PROJECT_ID"))
    projection = project_runtime_daily(reports, **kwargs)
    if error:
        projection["read_errors"].append(error)
    if not projection["records"][0]["runs"]:
        return "no_attributable_runs"
    request = Request(origin + "/api/runtime-daily/sync", method="POST",
        data=json.dumps(projection, separators=(",", ":")).encode(), headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json",
            "User-Agent": "QuantStrategyLab-RuntimeDaily/1.0", "X-QSL-Source-Binding-ID": source_binding,
        })
    try:
        with (opener or build_opener(_NoRedirect())).open(request, timeout=20) as response:
            body = response.read(4097)
            if not 200 <= response.status < 300 or len(body) > 4096:
                return "unconfirmed"
            ack = json.loads(body)
        expected = {"ok": True, "stored": True, "platform": PLATFORM, "account_key": account_key,
                    "target_key": target_key, "business_date": day.isoformat(), "observed_at": projection["observed_at"]}
        if not isinstance(ack, dict) or any(type(ack.get(k)) is not type(v) or ack[k] != v for k, v in expected.items()):
            return "unconfirmed"
    except Exception:
        return "unconfirmed"
    return "published"


def main():
    try:
        if sys.argv[1:] == ["--preflight"]:
            status = auth_preflight(os.environ)
        elif not sys.argv[1:]:
            status = publish_once(os.environ)
        else:
            raise ValueError("invalid_mode")
    except Exception:
        status = "unavailable"
    print(json.dumps({"status": status}, separators=(",", ":")))
    return 0 if status in {"published", "verified"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
