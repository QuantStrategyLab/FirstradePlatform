# FirstradePlatform

FirstradePlatform is an experimental Firstrade execution runtime inside QuantStrategyLab. It takes strategy and snapshot artifacts published by other QSL repositories and turns them into Firstrade-compatible orders, notifications, and reconciliation output, so a strategy that has already been researched elsewhere can be dry-run, and eventually live-traded, against a real Firstrade account. The underlying Firstrade API client is unofficial and reverse-engineered, so this platform leans on conservative defaults and explicit dry-run gating rather than convenience.

[Chinese README](README.zh-CN.md)

> Investing involves risk. This project does not provide investment advice and is for education, research, and engineering review only.

## What this repository is

FirstradePlatform is a QuantStrategyLab experimental Firstrade execution platform. It experiments with Firstrade-compatible US equity runtime execution for shared strategy packages.

It is an execution layer, not a strategy research repository. Strategy logic comes from `UsEquityStrategies`; snapshot and validation artifacts come from `UsEquitySnapshotPipelines` when a profile requires them.

## QSL architecture role

- **Layer**: `runtime-platform`.
- **Responsibility**: experimental Firstrade US equity execution runtime.
- **Owns**: Firstrade-compatible runtime controls, generated orders, notifications.
- **Consumes**: UsEquityStrategies, UsEquitySnapshotPipelines artifacts, QuantPlatformKit, QuantRuntimeSettings.
- **Must not**: promote strategies without evidence or store secrets in Git.

## Runtime boundary

- Loads only runtime-enabled strategy profiles exposed by the strategy packages.
- Handles broker/API connectivity, dry-run checks, notifications, and deployment settings.
- Must keep credentials in GitHub Secrets, cloud secret stores, or the broker-specific secret system, never in Git.
- Should start with dry-run or paper mode before any live order path is enabled.

## Live retry boundary

The runtime retries only a cycle that made **no** broker-order request: for
example, a temporary quote failure or insufficient settled cash. It writes a
durable create-only claim immediately before the first live broker request, so
an accepted, rejected, pending, timed-out, or otherwise unknown broker request
is never sent again automatically. Funding blocks notify once and can retry on
the next scheduled run inside the strategy window. Other zero-submission blocks
may use the bounded Scheduler error backoff.

Confirmed funding-only skips with no broker submission or pending reconciliation
are successful no-action cycles, with the insufficient-cash reason retained in
the report. They return HTTP 200 to avoid Scheduler error retries; the existing
later scheduled cycles can still retry after funds arrive. Quote failures, risk
blocks, technical errors and uncertain submissions retain their failure or
reconciliation status. The submission claim and live order gates are unchanged.

The pinned QuantPlatformKit revision includes the shared receipt fix that keeps
dry-run technical exceptions failed without claiming a live submission. Source
adoption still requires deploying the resulting image; a merged pin alone does
not update the serving runtime.

## Direct vs snapshot-backed profiles

Direct runtime profiles can usually run from market history or portfolio state. Snapshot-backed profiles need a current artifact bundle from the matching snapshot pipeline before this platform should execute them. The platform should not invent strategy eligibility; it should consume the status and artifacts published by the strategy and snapshot repositories.

## Deploy safely

1. Configure secrets and runtime variables outside Git.
2. Run the workflow or service in dry-run mode.
3. Review generated orders, logs, notifications, and reconciliation output.
4. Confirm rollback steps and artifact versions.
5. Enable scheduled or live execution only after the above checks are clear.

## Read-only account facts

The existing **Diagnose cached-balance stage failure** manual workflow can inspect saved
funds schemas with `run_readonly_diagnostic=true` and `funds_schema_only=true` at the exact
merged main SHA. Adding `funds_history_schema_only=true` inspects up to 64 existing funds
archives instead of the latest snapshots, using the same deployment identity and private
storage prefix. It reads generation-pinned objects only in the cloud runner and reports
dates, fixed metric labels/types, identity-match booleans and canonical snapshot hashes;
it never prints account identifiers, object paths or financial values. An incomplete listing,
invalid archive path or exceeded bound stops inspection. Identity comparisons follow the
deployed `QSL_RUNTIME_TARGET_JSON` / `RUNTIME_TARGET_JSON` single-account selector, then
`FIRSTRADE_ACCOUNT`; unresolved secret references stay unverified. Fixed native fields
include the provider's `result` envelope, without fuzzy field matching.
This does not call the broker, renew a session, import history, change
the serving revision or prove that historical snapshots contain returns, cash flows or trades.

The existing Runtime Target Lifecycle manual entry with `metadata_only=true` inspects the actual serving revision. It reports only whether the sync switch, approved destination, binding settings, token reference and cache settings are configured. It does not resolve secrets, read a cache or invoke the broker; binding correctness and cached-session validity remain explicitly unchecked. Configured settings do not prove a successful balance sync.

`POST /account-facts-sync` is a cached-session-only balance snapshot path. It is disabled unless `FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED=true`; it does not log in, refresh credentials, schedule itself, submit orders, or send notifications. It publishes only provider-reported equity and, when present, provider `cash_balance`; it does not read positions, so a positions read failure or incomplete positions cannot block the balance snapshot. Buying power and positions are not used to calculate assets or cash.

The existing daily Execution Report Heartbeat also invokes this snapshot path once through the already-configured Scheduler bridge. Its independent snapshot job checks the exact service URL, POST route and OIDC audience, never creates or updates the bridge, and requires a new successful Scheduler completion plus the matching Cloud Run request HTTP 200. The endpoint returns 200 only after the publisher accepts its acknowledgement; its INFO log may be suppressed and is not required. Dispatch acceptance alone is not success, and independent website latest/identity/history readback remains a separate acceptance check. The existing daily schedule is unchanged; report and notification jobs remain independent. A manual `snapshot_only=true` run checks this path without reports or notifications. Expired/missing cached authentication fails the snapshot job; `/session-renew` remains manual and is never invoked automatically. No Cloud Run image update is needed for this workflow-only integration.

The Cloud Run service must remain IAM-protected. The caller needs `roles/run.invoker` and must send its Google-signed ID token in `X-Serverless-Authorization`; the separate application token goes in `Authorization: Bearer …`. See [Cloud Run service-to-service authentication](https://cloud.google.com/run/docs/authenticating/service-to-service). Neither token is a Firstrade credential.

Configure the following only through protected Cloud Run environment variables and Secret Manager references: `FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED`, `FIRSTRADE_ACCOUNT_FACTS_SYNC_URL` (must equal the approved sync URL defined as `_APPROVED_SYNC_URL` in `application/account_facts_publisher.py`), `FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN` (a dedicated secret), `FIRSTRADE_ACCOUNT_FACTS_TARGET_ID`, `FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID`, `FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_KEY`, and `FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE`. The account identity is taken from the single exact selector in the protected runtime target; if `FIRSTRADE_ACCOUNT` is also set, it must match. All values must match the trusted QRS binding and current runtime target. Do not derive or invent a binding ID.

The existing manual Cloud Run environment sync carries the six non-token settings through the selected Firstrade target's `env` configuration in `CLOUD_RUN_SERVICE_TARGETS_JSON`; the legacy single-service path can use matching `FIRSTRADE_ACCOUNT_FACTS_*` inputs. Keep target, binding, account key, and scope in protected GitHub configuration. Set the protected GitHub variable `FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN_SECRET_NAME` to the name of the already-approved Secret Manager secret. The workflow maps that Secret Manager reference to `FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN`; it never accepts a raw token value from GitHub Actions. Keep the enable switch unset or `false` until deployment adoption is separately reviewed. This source change does not apply Cloud Run configuration or establish deployment adoption.

The existing `sync-cloud-run-env.yml` workflow also has a separate, default-off `sync_account_facts_configuration` dispatch input. It applies only the six `FIRSTRADE_ACCOUNT_FACTS_*` environment values and the existing token Secret Manager reference to the currently ready revision, after checking the protected runtime target, source SHA, approved ref, and secret state. It rejects combination with broad configuration sync, traffic promotion, cleanup, and diagnostic staging; it uses incremental updates with `--no-traffic` and verifies that unrelated configuration and active traffic remain unchanged. It does not create a secret or IAM binding. A successful run only stages configuration on a zero-traffic revision; serving adoption and a strict balance sync remain separate steps.

Illustrative synthetic configuration only:

```text
FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED=false
FIRSTRADE_ACCOUNT_FACTS_TARGET_ID=synthetic-target
FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_KEY=synthetic-account-key
FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE=US
FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN_SECRET_NAME=<approved-secret-name>
FIRSTRADE_ACCOUNT=synthetic-native-account-id  # optional; if set, must match runtime target
# FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN is injected only by the existing
# Secret Manager reference in the protected environment sync workflow.
```

Keep the sync disabled until the receiver-side account binding and separate sync token are configured and verified. The endpoint returns only a fixed status or error code; it never returns the native account ID.

## Repository layout

- `tests/`: unit, contract, and regression tests.
- `.github/workflows/`: CI, scheduled jobs, release, or deployment workflows.
- `scripts/`: operator scripts and local helpers.

## Quick start

```bash
uv sync --frozen --extra test
uv run --no-sync ruff check --exclude external .
uv run --no-sync python scripts/check_qpk_pin_consistency.py
```

## Useful docs

- [Isolated paper command consumer](docs/paper-execution-command-consumer.md)

## Community and security

- See [CONTRIBUTING.md](CONTRIBUTING.md) for pull request scope, local verification, and documentation expectations.
- Follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for maintainer and contributor conduct.
- Report credential, automation, broker, exchange, or cloud-resource vulnerabilities through [SECURITY.md](SECURITY.md); do not open public issues for secrets or live-execution risk.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). AI assistants and maintainers follow [AGENTS.md](AGENTS.md).

## License

See [LICENSE](LICENSE).

Display/notification naming configuration: [display names](docs/display-names.md).

Telegram delivery uses QPK notification intents and redacted receipts. Runtime
entrypoints install shared log and stream redaction. Environment-backed bot
routes resolve credentials only in QPK delivery; existing injected or in-memory
credentials use QPK's compatibility sender, with no temporary environment mutation.
Provider errors and credential-bearing request data are excluded from reports.
