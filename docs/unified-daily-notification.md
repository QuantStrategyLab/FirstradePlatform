# Unified daily notification (code only)

`QSL_ROUTINE_NOTIFICATION_MODE` defaults to `immediate`. Exact `daily_digest`
opts into one central bilingual digest. `QSL_DIGEST_ROUTINE_ORDERS=false` retains
immediate confirmed order notices. Failed, pending, uncertain, risk, persistence
and authentication outcomes retain immediate alerts. Healthy idle cycles and
routine plugin observations do not produce individual pushes.

QPK source reference: `5d0714201f3813f68a9833083cd21f6d61e5b702` (notification modules; same as the QPK pin).

The notification modules are imported directly from the QPK dependency pinned in
`pyproject.toml` and `uv.lock`; no vendored QPK notification snapshot is kept in
this repository. The pinned QPK revision is the single reviewed source for these
modules. Existing QPK/strategy pins, order execution, funding, risk, account
enablement and durable report writes are unchanged.

Before enabling routing, validate the exact active target's private archived
report, candidate store and QRS receiver. Existing cloud monitor wiring uses
`RUNTIME_TARGET_ENABLED=true`, exact protected `RUNTIME_TARGET_JSON`,
`EXECUTION_REPORT_GCS_URI`, `DIGEST_CANDIDATES_GCS_URI`, `DIGEST_STORE_TARGET`,
`DIGEST_OPAQUE_ACCOUNT_UID` and `DIGEST_TARGET_ID`. Missing configuration stops
projection; disabled targets stay disabled. Do not infer private identities.
The producer aggregates every unique cycle in the completed 06:00 SGT window;
missing evidence is unknown, never zero. No new collector or scheduler is added.

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
