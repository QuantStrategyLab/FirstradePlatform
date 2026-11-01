# Unified daily notification (code only)

Successful orders, no-trade cycles, healthy receipt checks and routine plugin
observations are recorded for the next scheduled daily digest in the selected
Chinese or English language, in both
notification modes. Actual unknown outcomes, failures, risk, persistence and
authentication incidents retain immediate alerts. A candidate or archive write
failure produces a separate localized coverage alert without a trade message.
The archive publisher and healthy-heartbeat coverage alert report fixed stage
and reason categories. Stages distinguish binding, storage initialization,
archive listing/read, projection, candidate history read/merge, serialization
and candidate write. Reasons distinguish authentication, permission denial,
missing objects, timeout, transport, invalid data, concurrency and unknown
failures. Exception messages, object paths, provider bodies and identities are
never copied into these diagnostics. The existing alert acknowledgement marker
still suppresses duplicate sends.

A missing candidate on the initial metadata read is the normal create path;
creation retains `if_generation_match=0`. Only explicit generation conflicts
use the existing bounded merge attempts. An uncertain write is never retried,
and `uploaded=false` after failure does not prove that storage was unchanged
or that earlier targets in the same invocation were not published. Read back
the affected object before any separately authorized retry. A stage/category
alone does not justify changing IAM or replaying old candidate windows.

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
