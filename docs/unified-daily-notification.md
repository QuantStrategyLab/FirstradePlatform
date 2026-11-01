# Unified daily notification (code only)

Successful orders, no-trade cycles, healthy receipt checks and routine plugin
observations are recorded for the next scheduled daily digest in the selected
Chinese or English language, in both
notification modes. Actual unknown outcomes, failures, risk, persistence and
authentication incidents retain immediate alerts. A candidate or archive write
failure produces a separate localized coverage alert without a trade message.
An accepted order with complete matching evidence is not a confirmed fill;
missing, contradictory or failed reconciliation evidence stays attention.

Notification-only source snapshot: QPK revision
`2b04882058666ebc81d1650d93fa2105ec4ba3d8`. The trading dependency remains
at its separate existing pin in `pyproject.toml` and `uv.lock`.

The notification snapshot is isolated from the trading dependency. Existing
QPK/strategy pins, order execution, funding, risk, account
enablement and durable report writes are unchanged.

Before deploying the policy, validate the exact active target's private archived
report, candidate store and QRS receiver. Existing cloud monitor wiring uses
`RUNTIME_TARGET_ENABLED=true`, exact protected `RUNTIME_TARGET_JSON`,
`EXECUTION_REPORT_GCS_URI`, `DIGEST_CANDIDATES_GCS_URI`, `DIGEST_STORE_TARGET`,
`DIGEST_OPAQUE_ACCOUNT_UID` and `DIGEST_TARGET_ID`. Missing configuration stops
projection; disabled targets stay disabled. Do not infer private identities.
The producer aggregates every unique cycle in the completed 06:00 SGT window;
missing evidence is unknown, never zero. No new collector or scheduler is added.
The manual healthy-heartbeat option cannot force an immediate success message.

`QSL_NOTIFICATION_MARKER_GCS_URI` optionally separates incident reservations,
delivery receipts and heartbeat event facts from `DIGEST_CANDIDATES_GCS_URI`.
Use a dedicated per-account prefix so the runtime needs mutable access only to
that state prefix and its exact daily candidate objects. Candidate paths and
the central receiver contract stay unchanged. When the variable is absent or
empty, state retains its existing paths under the candidate prefix. For an
already enabled target, preserve that location until its original reservations
and receipts have been migrated; changing the prefix alone would discard the
history used to prevent duplicate alerts. Per-target bindings may set the
variable independently.

No deployment, variable application, IAM, secret or live message is authorized
by this PR. Source adoption and delivery require separate approved release work.

For multiple active targets, protected `DIGEST_TARGET_BINDINGS_JSON` supplies a
list of per-target environment mappings. `DIGEST_NOTIFICATION_SCHEDULE_JSON`
can reuse the existing heartbeat scheduler/calendar declaration to prove a
window was not due. Missing due reports stay errors; closed configured sessions
produce an `actually_ran=false` marker without inventing zero trading counts.

Fill counts require individual execution identifiers. A completed order response
proves an order completed, not how many fills it received. Missing fill counts
remain unknown; confirmed routine orders still appear without a false diagnostic.

Runtime Guard notifications keep the localized summary and workflow link. Raw
provider audit entries remain in their original restricted logs and are not
passed as Telegram technical details, including when the deployed formatter is
pinned to an older shared package. Guard detection, deduplication and failure
exit codes are unchanged.

## Publication before the central daily read

GitHub schedule events can start late. A heartbeat workflow is therefore not a
reliable deadline trigger for the 07:00 Asia/Shanghai read. Keep its existing
monitoring checks, but run the archive-only publisher independently in an
approved Cloud Run Job using the platform image and its frozen dependencies.

Command: `/app/.venv/bin/python -m scripts.publish_digest_before_read`.
Use `--help` for an offline CLI check; it exits before publication or the watchdog.
The image keeps uv-managed Python under `/opt/python` so `appuser` can traverse
its path. Build checks and runtime commands use `/app/.venv/bin/python` directly;
the Docker build also checks publisher help after switching to `appuser`.
The command imports the existing producer, never the service entrypoint. It does
not run a drill, refresh a broker session, invoke a trading route, send Telegram,
or access Secret Manager. Archive reads use the Storage SDK and the job's
attached identity; the image does not need gcloud. Existing candidate writes,
merge semantics and identity checks remain authoritative.

Use one job per existing central target, with its exact protected runtime JSON,
archive prefix, opaque UID, target ID, destination and slot. No identifiers can
be inferred from a display label. Supply the existing notification schedule so
an idle candidate is only produced when the completed window was not due.
Multi-target binding JSON is rejected by this entrypoint: one successful target
cannot mask another missing target.

The job requires `DIGEST_PUBLISH_ENABLED=true`,
`QSL_ROUTINE_NOTIFICATION_MODE=daily_digest`, and the existing
`RUNTIME_TARGET_ENABLED=true`. These are publisher-job settings, not changes to
the trading service. Preserve the platform's existing unified-notification flag
and any notification marker prefix needed by its existing producer. Do not
inject broker credentials or Telegram token secrets into this job.

The deployment proposal is:

| Setting | Proposed value |
| --- | --- |
| Cloud Scheduler | `10 6 * * *`, timezone `Asia/Shanghai` |
| Scheduler target | OAuth-authenticated Cloud Run Jobs `:run` API for this job |
| Scheduler retries | 0; do not blindly retry an uncertain execution request |
| Job tasks / parallelism | 1 / 1 |
| Job retries | 0; an uncertain write is not proof of failure |
| Job task timeout | 600 seconds |
| Image | Reviewed immutable platform image containing this entrypoint |
| Publication window | Start at or after 06:00 and before 06:40 |
| In-process watchdog | 600 seconds, including projection and readback |
| Readback deadline | Before 06:55, leaving five minutes before the reader |

The business day is the day before the job's Shanghai date, for the completed
[06:00, 06:00) window. A normal run starts at 06:10 and finishes by 06:20. A late
start, disabled/missing binding, due-but-absent report, unconfirmed write, wrong
business day or identity, failed readback, or expired deadline exits nonzero.
An interrupted write remains unknown and must be inspected before any retry.
The success record contains only the business day and a verified-readback flag.

Deployment, Scheduler creation and IAM are separate operations requiring their
own authorization. The Scheduler identity needs permission to execute only the
job; the job identity needs the existing archive-read and candidate-store
permissions (including notification state used by the existing producer).
Do not substitute a project-wide role or copy the trading runtime's credentials.
A merged change or a successful unit test does not prove the job is deployed.

After a separately authorized rollout, verify a natural job execution and the
exact candidate object generation created before 07:00, then check the central
reader's date, slot, identity and coverage. A Scheduler API acceptance alone is
not publication success. Keep missing/invalid coverage visible; do not substitute
old candidates, zeros, or an artificial `not_due` result.

References: [GitHub schedule timing](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
and [Cloud Run scheduled jobs](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule).

Candidate persistence failures expose only a fixed `candidate_read` or
`candidate_write` stage and an allowlisted error category. Raw exception text,
resource paths and credential context stay out of command output. A permission
category identifies the failing storage operation; it does not establish that
an alerting Secret Manager denial caused the storage failure.

Account identifiers displayed in logs, notification results and account-selection
errors retain at most two trailing characters. Identifiers of two characters or
fewer are fully masked. Runtime failure alerts omit the exception detail.

Private state object and submission-claim locators retain their existing identity
format so a display change cannot bypass an existing duplicate-submission lock
or session-maintenance marker. New state payload account fields use the two-character
mask. State-store errors omit object locators and provider exception details.
