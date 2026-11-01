# Daily session renewal

`POST /session-renew` uses the existing IAM-protected Cloud Run service and the
existing daily 21:00 UTC Cloud Scheduler job. It remains disabled unless
`FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP=true`. It never runs a strategy or submits
orders. Successful renewal means account selection, balance read and shared
cache persistence completed; it does not establish trading eligibility or prove
an independent process can use the cache.

## Duplicate delivery and manual recovery

The runtime uses the existing `FIRSTRADE_GCS_STATE_BUCKET` and
`FIRSTRADE_STATE_PREFIX`. The dedicated object
`maintenance/session-renew-attempt.json` contains only a schema version, UTC
cycle, random execution ID, timestamps, status and fixed failure classification.
It does not contain authentication material or account data. Deployment must
first confirm that this path is unused, and that no other implementation writes
it. Do not reuse any trading or monthly session-check marker.

A generation-pinned read followed by a generation-matched write admits exactly
one attempt. A missing object is created with generation match zero. No login
starts if state cannot be read or the claim outcome is unknown. A completed
cycle suppresses duplicates; only a later daily cycle can start a new attempt.
`X-CloudScheduler-ScheduleTime` identifies the original scheduled cycle across
retries and midnight. It must be 21:00 UTC; old deliveries are acknowledged
without login, and future/malformed values are rejected. The header is not an
authentication mechanism: retain Cloud Run IAM and protected ingress. Requests
without the header use the most recent 21:00 UTC cycle, not a new manual attempt.

`running` has **no timeout takeover**. A crash, authentication failure, MFA or
new-device challenge, account-read failure, or uncertain cache/completion write
blocks subsequent automatic logins, including on later days. The route returns
`ok=false` on failure. A duplicate delivery can return HTTP 200 to acknowledge
that no new login was attempted; this is not a renewal success signal.

The renewal credential loader never reads MFA seed/code/PIN/email/phone fields.
Even caller-supplied MFA material is removed before constructing the client, and
SDK debug output is disabled. No challenge response, OTP request, seed-based
code generation, or login retry is attempted. Other independently authorized
client uses keep their existing behavior.

Recovery belongs to the owner. After inspecting the original attempt and
resolving authentication or storage outside this endpoint, the owner may
separately authorize a generation-matched transition of this object to `ready`.
Keep its existing `schema`, `cycle` and execution history; never delete the
object or backdate the cycle. Confirm there is no in-flight attempt first.
The next **later** daily cycle can then try once. No reset endpoint or automatic
reset is provided. A successful first cycle after `ready` sends one recovery
notification. Code rollback does not undo authentication side effects or clear
this maintenance state.

## Failure and deadline notifications

The runtime uses only the existing injected `TELEGRAM_TOKEN` and
`QSL_GLOBAL_TELEGRAM_CHAT_ID` (or `GLOBAL_TELEGRAM_CHAT_ID`). It does not resolve
broker secrets to send alerts. Messages contain fixed reason/stage, UTC cycle
and an action hint; they omit account, balance, exception text and credentials.
No new bot or token is required when this injection is already present.

The first failure attempts one notification. Atomic claims in
`maintenance/session-renew-alerts/YYYY-MM-DD/{failure,deadline,recovered}.json`
limit each event to one send attempt across instances. An acknowledged transport
response is recorded separately from an unknown/failed delivery; it is not
proof that a person received the notification. A crash after the claim, or a
failed/unknown delivery, is not automatically resent. If storage itself cannot
claim an alert, each incoming request makes one notification attempt and emits
`dedup_available=false`; cross-instance notification deduplication is unavailable
in that degraded state. Login remains blocked. Do not configure automatic
Scheduler retries as a substitute for notification recovery.

`POST /session-renew-deadline` reads only maintenance state and notification
configuration. It never loads broker credentials, reads session cookies, logs
in, renews, or publishes account facts. Schedule it at **21:15 UTC daily** using
the existing approved service root as OIDC audience. It requires a completed
record for that day's 21:00 cycle with `completed_at <= 21:15 UTC`. Missing,
older, running, failed and unreadable records alert; a completion after the
cutoff does not erase the missed deadline. Early calls do nothing and stale
Scheduler deliveries do not check a different cycle. The cutoff is exact;
Scheduler dispatch, logging and notification delivery may be delayed.

Structured stdout events use `event=session_renewal` and kinds `failure`,
`deadline`, `completed`, `recovered`. Notification events use
`event=session_renewal_notification` and `delivery=acknowledged|unconfirmed`.
Observe these fixed fields instead of treating HTTP 200 as renewal completion.

## Rollout requires separate owner approval

A merged PR does not deploy or activate monitoring. Prepare the following
concrete items in protected deployment material and obtain owner sign-off:

1. The reviewed image digest, current serving revision, rollback target and
   completion of any concurrent cache/deployment task. One deployment owner
   performs the rollout; do not deploy this branch alongside another release.
2. The exact existing bucket/prefix, unused maintenance paths, runtime identity
   and object read/create/update permissions. Overwriting GCS objects also needs
   `storage.objects.delete`. Reuse existing grants if sufficient; do not add
   project-wide roles. Keep these paths out of automated lifecycle deletion.
3. Existing Telegram target ID and token Secret reference, with receiver testing
   separately authorized. Never place either value in Git. Do not read the MFA
   seed or modify any authentication secret.
4. A distinct watchdog Scheduler job: POST to `/session-renew-deadline`, schedule
   `15 21 * * *`, timezone `UTC`, deadline `180s`, retries zero, existing approved
   Scheduler identity, OIDC audience equal to the service root. Do not change
   the existing renewal job's `0 21 * * *`, 600-second deadline or zero retries.
5. An independent Cloud Monitoring policy for failed watchdog delivery and
   unconfirmed notification events. A watchdog in the same service cannot send
   its own Telegram alert while the service is down. Activation is incomplete
   until this separate failure path has a verified notification channel. An
   empty channel inventory is a missing configuration, not a working alert.

Example Cloud Monitoring log-based policy input (replace placeholders only in
protected deployment material; this is not an apply command):

```json
{
  "displayName": "Session renewal watchdog delivery failure",
  "combiner": "OR",
  "conditions": [{
    "displayName": "Watchdog or notification transport failed",
    "conditionMatchedLog": {
      "filter": "(resource.type=\"cloud_scheduler_job\" AND resource.labels.job_id=\"<watchdog-job>\" AND jsonPayload.@type=\"type.googleapis.com/google.cloud.scheduler.logging.AttemptFinished\" AND severity>=ERROR) OR (resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"<service>\" AND jsonPayload.event=\"session_renewal_notification\" AND jsonPayload.delivery=\"unconfirmed\")"
    }
  }],
  "alertStrategy": {"notificationRateLimit": {"period": "3600s"}, "autoClose": "86400s"},
  "notificationChannels": ["projects/<project>/notificationChannels/<verified-channel-id>"],
  "enabled": true
}
```

This policy reports failed dispatch/transport, not a successful business cycle.
Also verify that the watchdog job stays enabled; a deleted or paused monitor has
no failed execution to alert on. Keep the existing external runtime guard as an
additional observer. Do not send test messages, trigger either endpoint, deploy,
change IAM or secrets merely to validate a PR.

Offline acceptance covers concurrent duplicate delivery, stale delivery across
midnight, state-write uncertainty, crash persistence, MFA/auth rejection,
future-day blocking, no MFA secret access, the deadline cutoff and notification
acknowledgement. After an approved rollout, separately authorize any test
notification and observe natural cycles. Do not trigger login for acceptance
without explicit authorization.

References: [Scheduler deduplication](https://docs.cloud.google.com/scheduler/docs/overview),
[GCS preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions),
[log-based alerting](https://docs.cloud.google.com/logging/docs/alerting/log-based-alerts).
