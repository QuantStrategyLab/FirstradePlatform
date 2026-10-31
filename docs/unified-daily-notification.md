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

## Scheduled heartbeat identity resolution

The heartbeat and completed-window projector use
`scripts/with_heartbeat_digest_binding.py` when running from the heartbeat workflow.
Existing complete workflow bindings and multi-target bindings retain their
current validation path. For an enabled single target with missing digest values,
the wrapper reads the sole 100% serving Cloud Run revision using the existing
workflow principal and explicit account. Its runtime identity must exactly match
the configured platform, profile, service, account selectors, scope and dry-run
mode. Only the existing plain-text digest UID, target, slot and destination are
consumed, in process memory; Secret Manager references are never dereferenced.
Configured values must agree with the serving binding. A changing traffic
observation is rejected. A missing schedule is taken only from the configured
runtime target's main schedule and timezone.

Missing source values return `digest_identity_owner_input_required` or
`digest_schedule_owner_input_required`. The owner must supply the approved
private binding; do not generate a UID or infer an identity from an account label.
Metadata read failures and identity conflicts remain explicit failures. This
wrapper does not change GitHub settings, runtime flags, receiver bindings,
notification routes or cloud permissions. It does not establish delivery success.
