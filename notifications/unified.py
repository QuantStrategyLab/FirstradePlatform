"""Opt-in bridge from existing runtime reports to the shared notification contract.

Only notification state is written. Broker, strategy and receipt acceptance code
remain outside this module. All storage/transport boundaries are injectable.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os

from quant_platform_kit.notifications import digest_archive
from quant_platform_kit.notifications.digest_candidates import project_reports
from quant_platform_kit.notifications.digest_routing import defer_routine_cycle_to_digest
from quant_platform_kit.notifications.unified_contract import (
    CandidatePersistenceError, IncidentTracker, NotificationEvent,
    effective_notification_mode, order_identity_digest, route_notification_event,
    sgt_business_window, unified_notification_enabled,
)

FLAG = "FIRSTRADE_UNIFIED_NOTIFICATION_ENABLED"
TARGET_KEYS = ("platform_id", "strategy_profile", "service_name", "account_scope",
               "account_selector", "deployment_selector", "dry_run_only")


def enabled(env=None):
    return unified_notification_enabled(os.environ if env is None else env, FLAG)


class GcsJsonStore:
    """Generation-checked JSON reads and CAS writes; no retry after uncertain IO."""
    def __init__(self, client=None):
        if client is None:
            from google.cloud import storage
            client = storage.Client()
        self.client = client

    def _blob(self, uri):
        if not isinstance(uri, str) or not uri.startswith("gs://"):
            raise ValueError("notification_store_configuration_missing")
        bucket, sep, name = uri[5:].partition("/")
        if not bucket or not sep or not name or any(c in uri for c in "?#*\n\r "):
            raise ValueError("notification_store_configuration_invalid")
        return self.client.bucket(bucket).blob(name)

    def read(self, uri):
        from google.api_core.exceptions import NotFound
        blob = self._blob(uri)
        try:
            blob.reload(retry=None, timeout=30)
        except NotFound:
            return None, None
        version = int(blob.generation)
        value = json.loads(blob.download_as_text(if_generation_match=version, retry=None, timeout=30))
        if not isinstance(value, dict):
            raise ValueError("notification_store_history_invalid")
        return version, value

    def compare_and_set(self, uri, version, value):
        from google.api_core.exceptions import PreconditionFailed
        try:
            self._blob(uri).upload_from_string(json.dumps(value, allow_nan=False),
                content_type="application/json", if_generation_match=0 if version is None else version,
                retry=None, timeout=30)
            return True
        except PreconditionFailed:
            return False


class MarkerStore:
    def __init__(self, store, prefix, observed_at):
        self.store, self.prefix, self.observed_at = store, prefix, observed_at

    def read(self, key):
        version, value = self.store.read(f"{self.prefix}/_notification_markers/{key}.json")
        if value and value.get("observed_at"):
            if datetime.fromisoformat(value["observed_at"]) > self.observed_at:
                raise ValueError("notification_observation_out_of_order")
        return version, value

    def compare_and_set(self, key, version, value):
        return self.store.compare_and_set(f"{self.prefix}/_notification_markers/{key}.json", version,
            {**value, "observed_at": self.observed_at.isoformat()})


def _bindings(env):
    raw = env.get("DIGEST_TARGET_BINDINGS_JSON", "")
    values = json.loads(raw) if raw else [{}]
    if not isinstance(values, list) or not values or not all(isinstance(v, dict) for v in values):
        raise ValueError("candidate_bindings_invalid")
    return [{**env, **v, "DIGEST_TARGET_BINDINGS_JSON": ""} for v in values]


def _binding(env, report):
    matches = []
    for binding in _bindings(env):
        target = json.loads(binding.get("RUNTIME_TARGET_JSON", "{}"))
        actual = report.get("runtime_target") or {}
        if all(target.get(key) == actual.get(key) for key in TARGET_KEYS):
            if binding.get("RUNTIME_TARGET_ENABLED") != "true":
                raise ValueError("candidate_target_enablement_unknown_or_disabled")
            if any(not target.get(key) for key in TARGET_KEYS if key != "dry_run_only"):
                raise ValueError("candidate_target_invalid")
            if target.get("platform_id") != "firstrade":
                raise ValueError("candidate_platform_mismatch")
            matches.append((binding, target))
    if len(matches) != 1:
        raise ValueError("candidate_source_identity_mismatch")
    binding, target = matches[0]
    if any(not str(binding.get(key) or "").strip() for key in (
        "DIGEST_OPAQUE_ACCOUNT_UID", "DIGEST_TARGET_ID", "DIGEST_CANDIDATES_GCS_URI", "DIGEST_STORE_TARGET")):
        raise ValueError("candidate_store_identity_missing")
    if not digest_archive._SAFE.fullmatch(binding["DIGEST_STORE_TARGET"]):
        raise ValueError("candidate_store_identity_invalid")
    return binding, target


def _event(report, binding, kind, *, reason=None, order_digest=None):
    stamp = datetime.fromisoformat(str(report.get("started_at", "")).replace("Z", "+00:00"))
    facts = (report.get("summary") or {}).get("notification_digest") or {}
    return NotificationEvent(kind, stamp, platform_id=report.get("platform"),
        opaque_account_uid=binding["DIGEST_OPAQUE_ACCOUNT_UID"], target_id=binding["DIGEST_TARGET_ID"],
        strategy_profile=report.get("strategy_profile"), source_run_id=report.get("run_id"),
        incident_reason=reason, order_execution_digest=order_digest,
        order_count=facts.get("order_count"), fill_count=facts.get("fill_count"))


def _row(report, binding, target, now):
    if report.get("dry_run") is True:
        # Preview quantities are never real orders or fills.
        event = _event(report, binding, "routine_cycle")
        if event.coverage_status != "known" or target.get("dry_run_only") is not True:
            raise ValueError("candidate_preview_identity_unknown")
        return dict(platform_id=event.platform_id, strategy_profile=event.strategy_profile,
            opaque_account_uid=event.opaque_account_uid, target_id=event.target_id,
            business_day=sgt_business_window(event.occurred_at).business_day.isoformat(),
            actually_ran=True, dry_run=True, as_of=event.occurred_at.isoformat(),
            status="ok" if report.get("status") == "ok" else "alert", execution_status="dry_run",
            order_count=None, fill_count=None, cycle_count=1, evidence_provenance="candidates",
            field_status={"order_count": "unknown", "fill_count": "unknown"},
            persistence_status="persisted", delivery_status="pending_central")
    row = project_reports([report], expected_target=target,
        opaque_account_uid=binding["DIGEST_OPAQUE_ACCOUNT_UID"], target_id=binding["DIGEST_TARGET_ID"],
        now=now)["runs"][0]
    row["field_status"] = {key: "known" if row[key] is not None else "unknown"
                           for key in ("order_count", "fill_count")}
    return row


def _aggregate(rows):
    identity = ("platform_id", "strategy_profile", "opaque_account_uid", "target_id", "business_day", "dry_run")
    if any(any(row.get(key) != rows[0].get(key) for key in identity) for row in rows):
        raise ValueError("candidate_identity_conflict")
    result = dict(rows[0])
    result["cycle_count"] = sum(row["cycle_count"] for row in rows)
    result["as_of"] = max(row["as_of"] for row in rows)
    for key in ("order_count", "fill_count"):
        result[key] = sum(row[key] for row in rows) if all(type(row.get(key)) is int for row in rows) else None
    result["field_status"] = {key: "known" if result[key] is not None else "unknown"
                              for key in ("order_count", "fill_count")}
    if any(row["status"] == "alert" for row in rows):
        result.update(status="alert", execution_status="execution_unknown", reason_code="source_cycle_alert")
    elif all(row.get("dry_run") is True for row in rows):
        result["execution_status"] = "dry_run"
    else:
        result["execution_status"] = "executed" if result["order_count"] else "no_action"
    return result


def _merge(store, uri, rows, events, *, idle=None, archive=False):
    for _ in range(8):
        version, previous = store.read(uri)
        # Read the former local ledger once for migration; new payloads store
        # all merge history in their per-run rows, as required by QRS.
        sources = dict((previous or {}).get("notification_source_runs") or {})
        facts = dict((previous or {}).get("notification_events") or {})
        if previous and "notification_source_runs" not in previous:
            for original in previous.get("runs", []):
                if original.get("actually_ran") is not True:
                    continue
                run_id = original.get("source_run_id")
                if not isinstance(run_id, str) or not run_id.strip():
                    if not archive:
                        raise ValueError("candidate_legacy_aggregate_requires_archive_refresh")
                    continue
                row = dict(original)
                for fact in row.pop("notification_events", []):
                    facts[fact["dedupe_key"]] = fact
                row.pop("source_run_id")
                sources[run_id] = row
        for row in sources.values():
            row["field_status"] = {key: "unknown" if value == "counts_unknown" else value
                                   for key, value in row.get("field_status", {}).items()}
        for run_id, row in rows.items():
            old = sources.get(run_id)
            if old and old != row:
                # Retain an observed alert or contradictory counts; never erase it
                # with a later healthy snapshot of the same source run.
                row = _aggregate([old, row])
                row.update(cycle_count=1, status="alert", reason_code="candidate_source_conflict")
                row.update(order_count=None, fill_count=None,
                    field_status={"order_count": "unknown", "fill_count": "unknown"})
            sources[run_id] = row
        facts.update(events)
        identity = ("platform_id", "strategy_profile", "opaque_account_uid", "target_id", "business_day", "dry_run")
        source_rows = list(sources.values())
        if any(any(row.get(key) != source_rows[0].get(key) for key in identity) for row in source_rows):
            raise ValueError("candidate_identity_conflict")
        by_run = {run_id: [] for run_id in sources}
        for key in sorted(facts):
            fact = facts[key]
            if fact.get("source_run_id") not in by_run:
                raise ValueError("candidate_event_source_run_missing")
            by_run[fact["source_run_id"]].append(fact)
        # QRS consumes only runs: each carries its own archive counts/events.
        run_rows = [{**sources[run_id], "source_run_id": run_id,
                     "notification_events": by_run[run_id]} for run_id in sorted(sources)]
        payload = dict(schema_version="qsl.digest_candidates.v1", producer_status="projected",
            runs=run_rows if sources else [idle] if idle else [])
        if store.compare_and_set(uri, version, payload):
            return True
    raise RuntimeError("candidate_concurrent_update")


def persist_report_candidate(report, fact, *, env, now, store):
    binding, target = _binding(env, report)
    row = _row(report, binding, target, now)
    if fact.get("event_kind") not in {"routine_cycle", "healthy_heartbeat", "order_submitted", "fill"}:
        row.update(status="alert", execution_status="execution_unknown", reason_code=fact.get("event_kind"))
    if not fact.get("dedupe_key"):
        raise ValueError("notification_candidate_identity_unknown")
    prefix = binding["DIGEST_CANDIDATES_GCS_URI"].rstrip("/")
    uri = f"{prefix}/firstrade/{row['business_day']}/{binding['DIGEST_STORE_TARGET']}.json"
    return _merge(store, uri, {report["run_id"]: row}, {fact["dedupe_key"]: fact})


def emit_candidates(env, *, now=None, read=digest_archive._read, store=None):
    if not enabled(env):
        return digest_archive.emit(env, now=now, read=read)
    current = now or datetime.now(timezone.utc)
    store = store or GcsJsonStore()
    results = []
    for binding in _bindings(env):
        reports = []
        def capture(args):
            value = read(args)
            if args[0] == "cat":
                reports.append(value)
            return value
        def upload(uri, body):
            payload = json.loads(body)
            target = json.loads(binding["RUNTIME_TARGET_JSON"])
            rows = {}
            events = {}
            for report in reports:
                try:
                    selected, _ = _binding(binding, report)
                except ValueError:
                    continue  # The existing archive projector already filters other targets.
                event = _event(report, selected, "routine_cycle")
                day = sgt_business_window(event.occurred_at).business_day.isoformat()
                if day != payload["runs"][0]["business_day"]:
                    continue
                rows[report["run_id"]] = _row(report, selected, target, current)
                events[event.to_candidate()["dedupe_key"]] = event.to_candidate()
            _merge(store, uri, rows, events, idle=payload["runs"][0] if not rows else None, archive=True)
        mapped = {**binding, "QSL_ROUTINE_NOTIFICATION_MODE": effective_notification_mode(binding, FLAG)}
        results.append(digest_archive.emit(mapped, now=current, read=capture, upload=upload))
    return {"status": "projected", "uploaded": any(r["uploaded"] for r in results), "targets": len(results)}


def _reserve(event, binding, *, now, store, state=None, recovered=False, severity=1):
    prefix = binding["DIGEST_CANDIDATES_GCS_URI"].rstrip("/")
    tracker = IncidentTracker(MarkerStore(store, prefix, now))
    decision = tracker.observe(event, state=state or event.event_kind, severity=severity, recovered=recovered)
    if decision.action == "coverage_unknown":
        raise ValueError("notification_incident_identity_unknown")
    return tracker, decision


def _independent_sender_receipt(receipt, decision, event, *, related_ids=()):
    """Keep opaque provider IDs; local reservation/event IDs are not receipts."""
    if not isinstance(receipt, str) or not receipt.strip():
        return None
    normalized = receipt.strip().casefold()
    namespaces = normalized.replace("/", ":").split(":")
    if any(part.strip().replace("_", "-") == "transport-ack" for part in namespaces):
        return None
    local_ids = (decision.incident_key, decision.transition_key,
                 event.to_candidate()["dedupe_key"], event.order_execution_digest, *related_ids)
    # Substring comparison also rejects namespaced IDs and marker object URIs.
    if any(value and value.casefold() in normalized for value in local_ids):
        return None
    return receipt


def _record_ack(tracker, decision, outcome, *, event, related_ids=()):
    acknowledged = outcome is True or isinstance(outcome, Mapping) and outcome.get("acknowledged") is True
    receipt = outcome.get("receipt_id") if isinstance(outcome, Mapping) and acknowledged else None
    receipt = _independent_sender_receipt(receipt, decision, event, related_ids=related_ids)
    # A boolean ACK has no unique receipt ID. Do not invent one or mark delivered.
    tracker.record_delivery(decision, receipt_id=receipt)
    return {"delivery_status": "delivered" if receipt else "uncertain",
            "transport_acknowledged": acknowledged}


def _tracked(event, binding, *, now, store, send, state=None, recovered=False, severity=1):
    tracker, decision = _reserve(event, binding, now=now, store=store, state=state,
                                 recovered=recovered, severity=severity)
    if decision.action != "send":
        return {"delivery_status": decision.action, "transport_acknowledged": False}
    try:
        outcome = send()
    except Exception:
        tracker.record_delivery(decision)
        raise
    return _record_ack(tracker, decision, outcome, event=event)


def cycle_kind(result):
    if result.get("auth_expired"):
        return "auth_failure"
    if result.get("notification_attention_reason_codes"):
        return "risk_escalation"
    if result.get("execution_blocked") or result.get("execution_blocking_skips"):
        return "blocked_run"
    orders = result.get("submitted_orders") or []
    if result.get("orders_pending_count") or result.get("pending_reconciliation"):
        return "order_status_unknown"
    if result.get("ok") is False or any(result.get(k) for k in (
        "error", "errors", "strategy_run_persistence_error", "report_persistence_error", "strategy_plugin_error",
        "strategy_plugin_alert_error", "notification_error")):
        return "real_error"
    if result.get("dry_run_only") is True and str(result.get("execution_status") or "").lower() in {
        "dry_run", "dry_run_completed", "no_action", "completed"}:
        return "routine_cycle"
    evidence = dict(result)
    if not evidence.get("execution_status") and result.get("ok") is True:
        evidence["execution_status"] = "completed"
    if defer_routine_cycle_to_digest(evidence, submitted_order_count=len(orders), submitted_orders=orders,
                                    mode="daily_digest", include_orders=True):
        return "fill" if orders else "routine_cycle"
    return "order_status_unknown" if orders else "real_error"


def notify_cycle(report, result, *, settings, send, send_failure=None, env=None, now=None, store=None):
    env = os.environ if env is None else env
    if not enabled(env):
        return {"route": "legacy", "candidate_persisted": False, "transport_acknowledged": send() is True}
    current = now or datetime.now(timezone.utc)
    kind = cycle_kind(result)
    try:
        binding, _ = _binding(env, report)
        store = store or GcsJsonStore()
        orders = result.get("submitted_orders") or []
        ids = [str(o.get("broker_order_id") or "") for o in orders]
        digest = order_identity_digest(json.dumps(sorted(ids))) if ids and all(ids) else None
        if kind in {"order_submitted", "fill"} and not digest:
            kind = "order_status_unknown"
        event = _event(report, binding, kind, reason="cycle_" + kind, order_digest=digest)
        if digest:
            event = replace(event, incident_reason="order_" + digest + "_" + kind)
        def immediate():
            return _tracked(event, binding, now=current, store=store, send=send,
                severity=2 if str(result.get("risk_level") or "").upper() == "HALT" else 1)
        def persist(fact):
            try:
                return persist_report_candidate(report, fact, env=env, now=current, store=store)
            except Exception as exc:
                raise CandidatePersistenceError("notification_candidate_write_unconfirmed") from exc
        routed = route_notification_event(event, env={**env, "QSL_DIGEST_ROUTINE_ORDERS": "false"}, flag_key=FLAG,
            persist_candidate=persist,
            legacy=send, immediate=immediate)
        if kind == "routine_cycle" and routed.candidate_persisted:
            for reason in ("cycle_real_error", "cycle_blocked_run", "cycle_auth_failure", "notification_candidate_write_failure"):
                try:
                    _tracked(replace(event, incident_reason=reason), binding, now=current, store=store,
                             send=send, state="healthy_cycle", recovered=True)
                except Exception:
                    print("notification_recovery_unconfirmed", flush=True)
        if kind in {"order_submitted", "fill"} and digest:
            _tracked(replace(event, incident_reason="order_" + digest + "_order_status_unknown"),
                     binding, now=current, store=store, send=send, state="confirmed_order", recovered=True)
        delivery = routed.result if isinstance(routed.result, dict) else {}
        return {"route": routed.route, "candidate_persisted": routed.candidate_persisted, **delivery}
    except CandidatePersistenceError:
        # Store failure is an alert, never a healthy zero-trade claim.
        try:
            failure = replace(event, event_kind="real_error", incident_reason="notification_candidate_write_failure")
            delivery = _tracked(failure, binding, now=current, store=store, send=send_failure or send)
            return {"route": "immediate_fallback", "candidate_persisted": False, **delivery}
        except Exception:
            print("notification_consolidation_failed; coverage_unknown", flush=True)
            return {"route": "failed", "candidate_persisted": False, "transport_acknowledged": False}
    except Exception:
        print("notification_consolidation_failed; coverage_unknown", flush=True)
        # Unreadable marker state must never result in an untracked retry.
        return {"route": "failed", "candidate_persisted": False, "transport_acknowledged": False}


def notify_heartbeat(*, kind, send, now, env=None, store=None, recovery_reports=(), read=digest_archive._read,
                     incident_reason="execution_receipt", affected_keys=None, service_name=None):
    env = os.environ if env is None else env
    if not enabled(env):
        return {"candidate_persisted": False, "fallback_acknowledged": send() is True}
    try:
        store = store or GcsJsonStore()
        persisted = False
        try:
            persisted = emit_candidates(env, now=now, read=read, store=store)["uploaded"] is True
        except Exception:
            if kind == "healthy_heartbeat":
                kind, incident_reason = "real_error", "notification_candidate_write_failure"
        if kind == "healthy_heartbeat" and not persisted:
            kind, incident_reason = "real_error", "notification_candidate_write_failure"
        decisions = []
        for binding in _bindings(env):
            if binding.get("RUNTIME_TARGET_ENABLED") == "false":
                continue
            target = json.loads(binding.get("RUNTIME_TARGET_JSON", "{}"))
            _, target = _binding(binding, {"runtime_target": target})
            if service_name is not None and target["service_name"] != service_name:
                continue
            from quant_platform_kit.common.runtime_heartbeat_policy import target_key
            key = target_key({**target, "service": target["service_name"]})
            if affected_keys is not None and key not in affected_keys and target["service_name"] not in affected_keys:
                continue
            event = NotificationEvent(kind, now, platform_id=target["platform_id"],
                opaque_account_uid=binding["DIGEST_OPAQUE_ACCOUNT_UID"], target_id=binding["DIGEST_TARGET_ID"],
                strategy_profile=target["strategy_profile"], incident_reason=incident_reason,
                source_run_id=env.get("GITHUB_RUN_ID"))
            if kind == "healthy_heartbeat":
                _tracked(replace(event, incident_reason="notification_candidate_write_failure"), binding,
                         now=now, store=store, send=send, state="candidate_write_confirmed", recovered=True)
                matched_recovery = any(all((report.get("runtime_target") or {}).get(key) == target.get(key)
                                           for key in TARGET_KEYS) for report in recovery_reports)
                if matched_recovery:
                    _tracked(event, binding, now=now, store=store, send=send, state="accepted", recovered=True)
            else:
                # Reserve each scope, then one callback for the combined alert.
                tracker, decision = _reserve(event, binding, now=now, store=store)
                decisions.append((tracker, decision, event))
                if decision.action == "send":
                    try:
                        fact = event.to_candidate()
                        digest = hashlib.sha256(json.dumps(fact, sort_keys=True).encode()).hexdigest()
                        uri = binding["DIGEST_CANDIDATES_GCS_URI"].rstrip("/") + f"/_notification_events/{digest}.json"
                        version, previous = store.read(uri)
                        if previous != fact and not store.compare_and_set(uri, version, fact):
                            raise RuntimeError("notification_event_concurrent_update")
                    except Exception:
                        persisted = False
        delivery = []
        claimed = [(tracker, decision, event) for tracker, decision, event in decisions if decision.action == "send"]
        if claimed:
            try:
                outcome = send()
            except Exception:
                for tracker, decision, event in claimed:
                    tracker.record_delivery(decision)
                raise
            # One sender response covers the combined alert. A local identity
            # from any reservation is invalid for every reservation it covers.
            related_ids = tuple(value for _, decision, event in claimed for value in (
                decision.incident_key, decision.transition_key,
                event.to_candidate()["dedupe_key"], event.order_execution_digest) if value)
            for tracker, decision, event in claimed:
                delivery.append(_record_ack(tracker, decision, outcome, event=event, related_ids=related_ids))
        return {"candidate_persisted": persisted, "deliveries": delivery}
    except Exception:
        print("notification_consolidation_failed; coverage_unknown", flush=True)
        return {"candidate_persisted": False, "failed": True}


def alert_text(kind, locale):
    messages = {
        "receipt_missing": ("执行回执缺失。", "Execution receipt is missing."),
        "receipt_unacceptable": ("执行回执需要检查。", "Execution receipt needs review."),
        "real_error": ("运行发生异常。", "The runtime encountered an error."),
        "candidate_write_failure": ("通知候选未能确认写入。", "Notification candidate storage could not be confirmed."),
    }
    zh = str(locale or "").strip().lower().startswith("zh")
    title = messages.get(kind, messages["receipt_unacceptable"])[0 if zh else 1]
    return title + ("\n请查看本次运行记录。" if zh else "\nReview the current run record.")
