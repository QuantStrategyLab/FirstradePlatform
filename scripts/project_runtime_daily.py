"""Project only account-bound private reports into the existing daily contract."""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from quant_platform_kit.common.execution_receipts import attach_execution_receipt

PLATFORM = "firstrade"


def project_runtime_daily(reports, *, target_key, account_binding, business_date, observed_at):
    """Retain verified reports; unknown coverage and unbound reports remain explicit."""
    parts = target_key.split("|") if isinstance(target_key, str) else []
    if (len(parts) != 3 or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", part) for part in parts)
            or not isinstance(account_binding, str) or not re.fullmatch(r"[a-f0-9]{64}", account_binding)):
        raise ValueError("daily_binding_invalid")
    if not isinstance(reports, (list, tuple)) or len(reports) > 20:
        raise ValueError("daily_report_limit")
    try:
        day = date.fromisoformat(business_date)
    except (ValueError, TypeError):
        raise ValueError("daily_date_invalid") from None
    if (day.isoformat() != business_date or not isinstance(observed_at, datetime)
            or observed_at.tzinfo is None or observed_at.utcoffset() is None
            or day > observed_at.astimezone(ZoneInfo("America/New_York")).date()):
        raise ValueError("daily_date_invalid")
    iso = observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    service, profile, scope = parts
    runs, unmatched, excluded, conflicts = [], [], [], []
    seen = set()
    for report in reports:
        if not isinstance(report, dict):
            unmatched.append({"run_id": None, "platform": None, "service": None,
                              "strategy_profile": None, "account_scope": None, "reason": "identity_unverified"})
            continue
        target = report.get("runtime_target") or {}
        if not isinstance(target, dict):
            target = {}
        anchor = report.get("account_facts_source_binding_id")
        matches = (report.get("platform") == PLATFORM and report.get("service_name") == service
                   and report.get("strategy_profile") == profile and report.get("account_scope") == scope
                   and anchor == account_binding
                   and all(target.get(key) in (None, expected) for key, expected in
                           (("service_name", service), ("strategy_profile", profile), ("account_scope", scope))))
        if not matches:
            unmatched.append({"run_id": None, "platform": None, "service": None,
                              "strategy_profile": None, "account_scope": None, "reason": "identity_unverified"})
            continue
        try:
            start = datetime.fromisoformat(report["started_at"].replace("Z", "+00:00"))
            finish = datetime.fromisoformat(report["finished_at"].replace("Z", "+00:00"))
            if start.tzinfo is None or finish.tzinfo is None or finish < start or finish > observed_at:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            excluded.append({"run_id": None, "reason": "invalid_run_time"})
            continue
        if start.astimezone(ZoneInfo("America/New_York")).date() != day:
            excluded.append({"run_id": None, "reason": "other_business_date"})
            continue
        run = report.get("run_id")
        errors = report.get("errors")
        if not isinstance(run, str) or not run or not isinstance(errors, list):
            unmatched.append({"run_id": None, "platform": None, "service": None,
                              "strategy_profile": None, "account_scope": None, "reason": "invalid_report"})
            continue
        opaque = "run." + hashlib.sha256((target_key + "\0" + run).encode()).hexdigest()[:32]
        if opaque in seen:
            if opaque not in conflicts:
                conflicts.append(opaque)
            continue
        seen.add(opaque)
        mode = target.get("execution_mode")
        dry = report.get("dry_run")
        lane = "dry_run" if dry is True else mode if dry is False and mode in {"live", "paper"} else "insufficient"
        status = report.get("status")
        if status not in {"ok", "skipped", "success", "completed", "no_action", "error", "failed", "failure", "cancelled", "canceled", "timed_out"}:
            status = None
        receipt = None
        try:
            if report.get("execution_receipt") is not None:
                receipt = attach_execution_receipt(dict(report), report["execution_receipt"])["execution_receipt"]
                received = datetime.fromisoformat(receipt["observed_at"].replace("Z", "+00:00"))
                if received.tzinfo is None or not start.replace(microsecond=0) <= received <= finish:
                    receipt = None
        except (TypeError, ValueError, KeyError):
            receipt = None
        if receipt and (errors or status in {"error", "failed", "failure", "cancelled", "canceled", "timed_out"}):
            receipt = None
        if receipt and status == "skipped" and receipt["outcome"] in {"submitted", "filled"}:
            receipt = None
        activity = ({"no_action": "no_submission", "no_signal": "no_signal", "no_rebalance": "no_rebalance",
                     "submitted": "submitted", "filled": "filled", "reconciliation_required": "reconciliation_required",
                     "risk_blocked": "blocked", "failed": "failed"}.get(receipt["outcome"], "unknown")
                    if receipt and lane in {"live", "paper"} else "unknown")
        runs.append({"run_id": opaque, "source_object": None, "source_objects": [],
                     "started_at": start.isoformat().replace("+00:00", "Z"),
                     "finished_at": finish.isoformat().replace("+00:00", "Z"), "object_updated_at": None,
                     "report_status": status, "execution_lane": lane, "activity": activity, "run_time_known": True,
                     "evidence": {"execution_status": None, "broker_submission_done": None, "action_done": None,
                                  "orders_pending_count": None, "errors_present": bool(errors),
                                  "receipt_outcome": receipt["outcome"] if receipt else None,
                                  "receipt_broker_confirmation": receipt["broker_confirmation"] if receipt else None}})
    lanes = {item["execution_lane"] for item in runs}
    record = {"platform": PLATFORM, "target_key": target_key,
              "target": {"service": service, "strategy_profile": profile, "account_scope": scope},
              "business_date": business_date, "timezone": "America/New_York", "observed_at": iso,
              "status": "conflict" if conflicts else "read_incomplete", "kind": "incomplete",
              "completeness": "incomplete", "execution_lane": next(iter(lanes)) if len(lanes) == 1 else "insufficient",
              "schedule": {"state": "unevaluable", "business_date": business_date, "timezone": "America/New_York",
                           "latest_due_at": None, "next_due_at": None, "grace_ends_at": None,
                           "publication_grace_ended": None, "expected_window": "unspecified", "reason": None},
              "runs": runs, "excluded_reports": excluded, "conflicts": conflicts,
              "fills": {"source": "not_connected", "records": [], "count": None}}
    return {"platform": PLATFORM, "observed_at": iso, "completeness": "incomplete",
            "read_errors": ["coverage_unconfirmed"], "records": [record], "unmatched_reports": unmatched}
