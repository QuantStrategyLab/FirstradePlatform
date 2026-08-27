# Firstrade isolated paper command consumer

`POST /paper-command-consumer` manually verifies delayed paper commands. It is
not called by `/run`, `/dry-run`, scheduler workflows, or the normal strategy
cycle, and it never constructs an execution port or order request.

The endpoint uses the shared QuantPlatformKit paper lifecycle: approved release
receipt, exact platform/account-scope/strategy-profile binding, create-only
claim and events, paper-risk receipt, and an enforced command gate. Only after
those checks pass does it open a read-only Firstrade session for current
balances, all positions, and quotes.

## Required isolation

- `FIRSTRADE_PAPER_EXECUTION_COMMAND_CONSUMER_ENABLED=true`
- `RUNTIME_TARGET_ENABLED=false`
- `FIRSTRADE_DRY_RUN_ONLY=true`
- `RUNTIME_TARGET_JSON.execution_mode=paper`
- `CASH_ONLY_EXECUTION=true`
- explicit `FIRSTRADE_ACCOUNT`
- `FIRSTRADE_EXECUTION_COMMAND_CLOUD_URI` or
  `FIRSTRADE_EXECUTION_COMMAND_DIR`

The exact account identifier is required for this endpoint even if Firstrade
currently returns one account; this prevents a newly added account from being
selected implicitly. The consumer binds logical delivery using the runtime
target's `account_scope`, never command-provided metadata.

## Fail-closed reconciliation

The account read includes all positions rather than only the strategy's managed
symbols. Missing cash/current market values, an unmanaged position, a short,
a stale or invalid quote, a mismatch between cash-plus-positions and equity, or
a release/binding mismatch records a blocked or rejected paper event. The
consumer never guesses a quantity, adjusts leverage, or submits a broker order.

Turn the flag off again after a manual verification. Live rollout remains a
separate, reviewed release-readiness decision.
