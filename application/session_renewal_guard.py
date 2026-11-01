"""Single-writer renewal state and a broker-free daily deadline check."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable

from application.firstrade_client import FirstradeMfaRequired
from application.session_check_service import SessionRenewalFailure
from application.state_persistence import GcsStateStore, StatePersistenceError

ATTEMPT_KEY = "maintenance/session-renew-attempt.json"
ALERT_PREFIX = "maintenance/session-renew-alerts"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def cycle_at(now: datetime) -> datetime:
    now = now.astimezone(timezone.utc)
    start = now.replace(hour=21, minute=0, second=0, microsecond=0)
    return start if now >= start else start - timedelta(days=1)


def parse_time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result.astimezone(timezone.utc)


class RenewalStore:
    """Generation-pinned reads and compare-and-swap; no lease expiry or takeover."""

    def __init__(self, state: GcsStateStore):
        self.state = state

    @classmethod
    def from_env(cls):
        bucket = os.getenv("FIRSTRADE_GCS_STATE_BUCKET", "").strip()
        if not bucket:
            raise StatePersistenceError("Renewal state unavailable")
        return cls(GcsStateStore(bucket, os.getenv("FIRSTRADE_STATE_PREFIX", "firstrade-platform")))

    def _blob(self):
        from google.cloud import storage

        uri = self.state._object_uri(ATTEMPT_KEY)
        bucket, _, name = uri[5:].partition("/")
        return storage.Client().bucket(bucket).blob(name)

    def read(self):
        from google.api_core.exceptions import NotFound

        blob = self._blob()
        try:
            blob.reload(timeout=15, retry=None)
        except NotFound:
            return None, 0
        generation = int(blob.generation)
        # A concurrent replacement is a failed read, never interpreted as absence.
        value = json.loads(blob.download_as_text(
            if_generation_match=generation, timeout=15, retry=None,
        ))
        if not isinstance(value, dict) or value.get("schema") != 1:
            raise StatePersistenceError("Invalid renewal state")
        if value.get("status") not in {"running", "completed", "manual_required", "ready"}:
            raise StatePersistenceError("Invalid renewal state")
        cycle = parse_time(value["cycle"])
        if cycle != cycle_at(cycle):
            raise StatePersistenceError("Invalid renewal cycle")
        if value["status"] == "completed" and parse_time(value["completed_at"]) < cycle:
            raise StatePersistenceError("Invalid renewal completion")
        return value, generation

    def write(self, value, generation):
        from google.api_core.exceptions import PreconditionFailed

        blob = self._blob()
        try:
            blob.upload_from_string(json.dumps(value), content_type="application/json",
                                    if_generation_match=generation, timeout=15, retry=None)
        except PreconditionFailed:
            return None
        # The SDK populates generation from the upload response. Never do a fresh reload:
        # it might observe another writer and grant authority over that writer's record.
        if not blob.generation:
            raise StatePersistenceError("Renewal write outcome unknown")
        return int(blob.generation)

    def claim_alert(self, cycle: str, kind: str):
        date = parse_time(cycle).strftime("%Y-%m-%d")
        key = f"{ALERT_PREFIX}/{date}/{kind}.json"
        if self.state.create_json(key, {
            "schema": 1, "cycle": cycle, "kind": kind, "attempted_at": utcnow().isoformat(),
            "delivery": "unknown",
        }):
            return True
        receipt = self.state.read_json(key) or {}
        return "already_acknowledged" if receipt.get("delivery") == "acknowledged" else "unconfirmed"

    def finish_alert(self, cycle, kind, delivery):
        date = parse_time(cycle).strftime("%Y-%m-%d")
        self.state.write_json(f"{ALERT_PREFIX}/{date}/{kind}.json", {
            "schema": 1, "cycle": cycle, "kind": kind, "delivery": delivery,
            "finished_at": utcnow().isoformat(),
        })


def emit_event(kind: str, cycle: str, reason: str, stage: str):
    # Deliberately exclude exceptions, broker responses, accounts and auth data.
    print(json.dumps({"severity": "INFO" if kind == "recovered" else "ERROR",
                      "event": "session_renewal", "kind": kind, "cycle": cycle,
                      "reason": reason, "stage": stage}), flush=True)


def send_alert(kind: str, cycle: str, reason: str, stage: str) -> bool:
    """Use only the existing injected notification credentials, never broker secrets."""
    from notifications.telegram import build_sender

    token = os.getenv("TELEGRAM_TOKEN")
    chat = os.getenv("QSL_GLOBAL_TELEGRAM_CHAT_ID") or os.getenv("GLOBAL_TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    titles = {"failure": "Firstrade 会话续期失败", "deadline": "Firstrade 会话续期未在 21:15 UTC 前完成",
              "recovered": "Firstrade 会话续期已恢复"}
    action = "请检查人工认证或存储状态；系统不会自动重试登录。"
    if kind == "deadline":
        action = "请检查调度、续期记录和存储；不要重复触发登录。"
    elif kind == "recovered":
        action = "账户验证与共享缓存保存已完成。"
    return build_sender(token, chat)(
        f"{titles[kind]}\n周期：{cycle}\n原因：{reason}\n阶段：{stage}\n{action}"
    ) is True


def alert(store, *, kind, cycle, reason, stage, sender=send_alert):
    emit_event(kind, cycle, reason, stage)
    try:
        claimed = store.claim_alert(cycle, kind) if store is not None else None
        if claimed in {"already_acknowledged", "unconfirmed"}:
            if claimed == "unconfirmed":
                print(json.dumps({"severity": "ERROR", "event": "session_renewal_notification",
                                  "kind": kind, "cycle": cycle, "delivery": "unconfirmed",
                                  "dedup_available": True}), flush=True)
            return claimed
    except Exception:
        # Storage failure must not also silence the notification. In this degraded
        # case each incoming request attempts one send; it still never logs in.
        claimed = None
    try:
        acknowledged = sender(kind, cycle, reason, stage) is True
    except Exception:
        acknowledged = False
    delivery = "acknowledged" if acknowledged else "unconfirmed"
    if claimed is True:
        try:
            store.finish_alert(cycle, kind, delivery)
        except Exception:
            pass  # Never resend a notification whose delivery may have succeeded.
    print(json.dumps({"severity": "INFO" if acknowledged else "ERROR",
                      "event": "session_renewal_notification", "kind": kind, "cycle": cycle,
                      "delivery": delivery, "dedup_available": claimed is not None}), flush=True)
    return delivery


def run_guarded_renewal(
    renew: Callable, *, store=None, schedule_time=None, now=None, clock=utcnow, sender=send_alert,
):
    """At most one login for a daily cycle; any unknown outcome needs owner recovery."""
    now = now or clock()
    cycle = cycle_at(now).isoformat()
    if schedule_time:
        try:
            scheduled = parse_time(schedule_time)
            if scheduled != cycle_at(scheduled) or scheduled > now:
                raise ValueError("Invalid renewal schedule time")
            if scheduled != cycle_at(now):
                return {"ok": False, "status": "stale_delivery", "session_renewed": False}, 200
        except (ValueError, TypeError):
            return {"ok": False, "error": "invalid_schedule_time"}, 400
    try:
        store = store or RenewalStore.from_env()
        previous, generation = store.read()
        if previous:
            old_cycle = parse_time(previous["cycle"])
            status = previous["status"]
            if status not in {"completed", "ready"}:
                return {"ok": False, "status": "not_replayed", "session_renewed": False,
                        "manual_recovery_required": True}, 200
            if old_cycle >= parse_time(cycle):
                return {"ok": status == "completed", "status": "duplicate_delivery",
                        "session_renewed": False}, 200
        attempt = {"schema": 1, "cycle": cycle, "status": "running",
                   "execution_id": uuid.uuid4().hex, "started_at": now.isoformat(),
                   "manual_recovery_required": True}
        claimed_generation = store.write(attempt, generation)
        if claimed_generation is None:
            return {"ok": False, "status": "concurrent_delivery", "session_renewed": False}, 200
    except Exception:
        delivery = alert(store, kind="failure", cycle=cycle,
                         reason="guard_state_unavailable", stage="guard", sender=sender)
        return {"ok": False, "error": "guard_state_unavailable", "alert_delivery": delivery}, 503

    try:
        result = renew()
        if result.get("ok") is not True or result.get("session_renewed") is not True:
            raise SessionRenewalFailure("renewal_result_invalid", "validation")
    except Exception as exc:
        reason, stage = "renewal_outcome_unknown", "auth"
        if isinstance(exc, FirstradeMfaRequired):
            reason = "mfa_or_device_verification_required"
        elif isinstance(exc, SessionRenewalFailure):
            reason, stage = exc.reason, exc.stage
        failed = {**attempt, "status": "manual_required", "reason": reason, "stage": stage,
                  "finished_at": clock().isoformat()}
        try:
            store.write(failed, claimed_generation)
        except Exception:
            pass  # The durable running record still prevents replay, including on future days.
        delivery = alert(store, kind="failure", cycle=cycle, reason=reason, stage=stage, sender=sender)
        return {"ok": False, "error": reason, "stage": stage, "manual_recovery_required": True,
                "alert_delivery": delivery}, 409 if isinstance(exc, FirstradeMfaRequired) else 503

    completed = {**attempt, "status": "completed", "manual_recovery_required": False,
                 "completed_at": clock().isoformat()}
    try:
        if store.write(completed, claimed_generation) is None:
            raise StatePersistenceError("Completion conflict")
    except Exception:
        delivery = alert(store, kind="failure", cycle=cycle, reason="completion_outcome_unknown",
                         stage="completion", sender=sender)
        return {"ok": False, "error": "completion_outcome_unknown",
                "manual_recovery_required": True, "alert_delivery": delivery}, 503
    print(json.dumps({"severity": "INFO", "event": "session_renewal", "kind": "completed",
                      "cycle": cycle, "execution_id": attempt["execution_id"]}), flush=True)
    if previous and previous["status"] == "ready":
        alert(store, kind="recovered", cycle=cycle, reason="renewal_completed", stage="completion", sender=sender)
    return {**result, "cycle": cycle, "status": "completed"}, 200


def check_renewal_deadline(*, store=None, schedule_time=None, now=None, sender=send_alert):
    """Check the 21:15 UTC cutoff without loading credentials or invoking renewal."""
    now = now or utcnow()
    cycle = cycle_at(now)
    if schedule_time:
        try:
            scheduled = parse_time(schedule_time)
            if scheduled != cycle_at(scheduled) + timedelta(minutes=15) or scheduled > now:
                raise ValueError("Invalid deadline schedule time")
            cycle = cycle_at(scheduled)
            if cycle != cycle_at(now):
                return {"ok": False, "status": "stale_delivery"}, 200
        except (ValueError, TypeError):
            return {"ok": False, "error": "invalid_schedule_time"}, 400
    cutoff = cycle + timedelta(minutes=15)
    if now < cutoff:
        return {"ok": True, "status": "not_due"}, 200
    reason = "not_completed_by_2115_utc"
    try:
        store = store or RenewalStore.from_env()
        record, _ = store.read()
        if (record and record["cycle"] == cycle.isoformat() and record["status"] == "completed"
                and cycle <= parse_time(record["completed_at"]) <= cutoff):
            return {"ok": True, "status": "completed_by_deadline", "cycle": cycle.isoformat()}, 200
    except Exception:
        reason = "deadline_state_unavailable"
    delivery = alert(store, kind="deadline", cycle=cycle.isoformat(), reason=reason,
                     stage="deadline", sender=sender)
    # HTTP 200 acknowledges an observed incident, not successful renewal. Failed
    # notification transport remains non-2xx so Scheduler failure monitoring can see it.
    return {"ok": False, "error": reason, "cycle": cycle.isoformat(),
            "alert_delivery": delivery}, 200 if delivery in {"acknowledged", "already_acknowledged"} else 503
