# Firstrade Platform

> Risk warning: this project is not investment advice and is provided for study and engineering validation only.

Language: [English](README.md) | [中文](README.zh-CN.md)

---

Firstrade platform layer for QuantStrategyLab-style US equity runtimes.

This repository wraps the unofficial `firstrade` Python package and exposes a
QuantStrategyLab platform layer for shared `UsEquityStrategies` profiles. It
provides QuantPlatformKit broker ports for quotes, OHLC history, portfolio
snapshots, and guarded order submission.

## Status

- API source: unofficial, reverse-engineered `firstrade` package
- Upstream package: https://pypi.org/project/firstrade/
- Upstream repository: https://github.com/MaxxRK/firstrade-api
- Local default order mode: dry-run / preview only
- Live order path: blocked unless both CLI confirmation and
  `FIRSTRADE_ENABLE_LIVE_TRADING=true` are set
- Strategy domain: shared `us_equity` profiles from `UsEquityStrategies`

This project is not affiliated with, endorsed by, or supported by Firstrade
Securities Inc. The upstream API can break without notice when Firstrade
changes its web/mobile backend.

## Why This Exists

Firstrade does not expose the same kind of official retail trading API as
brokers such as IBKR or LongPort. The integration here is therefore treated as
an experimental platform layer, not a production-grade broker connector.

Use it first for:

- login/MFA validation
- account and position reads
- quote/OHLC checks
- dry-run order preview
- very small, explicitly approved live validation only after manual review

## Strategy Runtime Boundary

This platform is intended to mirror the role of `InteractiveBrokersPlatform`,
`CharlesSchwabPlatform`, and `LongBridgePlatform`: strategy logic stays in
`UsEquityStrategies`, while this repository owns Firstrade authentication,
account reads, market data reads, order translation, runtime safety controls,
and deployment wiring.

Firstrade is a first-class `platform_id` in `UsEquityStrategies`. It is treated
as a value-native US equity platform for strategy adapter purposes, so weight
strategies receive the same `portfolio_snapshot` input needed for platform-side
`weight -> value` translation.

Print the current Firstrade strategy matrix:

```bash
.venv/bin/python scripts/print_strategy_profile_status.py
```

## Environment

Copy `.env.example` into your secret manager or shell environment. Do not
commit credentials.

| Variable | Required | Description |
| --- | --- | --- |
| `FIRSTRADE_USERNAME` | Yes | Firstrade login username |
| `FIRSTRADE_PASSWORD` | Yes | Firstrade login password |
| `FIRSTRADE_MFA_SECRET` | Optional | TOTP secret for unattended MFA |
| `FIRSTRADE_PIN` | Optional | PIN flow supported by upstream package |
| `FIRSTRADE_MFA_EMAIL` | Optional | Email OTP recipient selector |
| `FIRSTRADE_MFA_PHONE` | Optional | SMS OTP recipient selector |
| `FIRSTRADE_MFA_CODE` | Optional | One-time OTP code for the current validation run |
| `FIRSTRADE_ACCOUNT` | Optional | Required when multiple accounts are returned |
| `STRATEGY_PROFILE` | Yes for runtime | Shared US equity strategy profile |
| `FIRSTRADE_DRY_RUN_ONLY` | Optional | Defaults to `true` for platform runtime |
| `FIRSTRADE_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS` | Optional | Override the supported strategy runtime execution window in trading days. Unset uses the strategy default |
| `FIRSTRADE_REUSE_SESSION` | Optional | Try cached Firstrade session headers before logging in again. Defaults to `false` |
| `FIRSTRADE_SESSION_CACHE_TTL_SECONDS` | Optional | Max age for local session header reuse when `FIRSTRADE_REUSE_SESSION=true`. Defaults to `21600` |
| `FIRSTRADE_PERSIST_SESSION_CACHE` | Optional | Persist Firstrade session headers to the configured GCS state bucket when `FIRSTRADE_REUSE_SESSION=true`. Defaults to `false` |
| `FIRSTRADE_GCS_STATE_BUCKET` | Optional | GCS bucket for runtime state JSON, including persisted session cache and account funds snapshots |
| `FIRSTRADE_STATE_PREFIX` | Optional | Object prefix within `FIRSTRADE_GCS_STATE_BUCKET`, default `firstrade-platform` |
| `FIRSTRADE_PERSIST_ACCOUNT_SNAPSHOT` | Optional | Persist compact masked account funds snapshots from `/session-check`. Defaults to `false` |
| `FIRSTRADE_PERSIST_STRATEGY_RUNS` | Optional | Persist `/run` strategy state, plans, and submitted/skipped order results to GCS. Defaults to `false` |
| `ACCOUNT_PREFIX` | Optional | Alert/log prefix, default `FIRSTRADE` |
| `ACCOUNT_REGION` | Optional | Runtime account scope, default `US` |
| `NOTIFY_LANG` | Optional | Notification language, `en` or `zh` |
| `TELEGRAM_TOKEN` | Optional | Telegram bot token for strategy-cycle summaries |
| `GLOBAL_TELEGRAM_CHAT_ID` | Optional | Telegram chat ID for strategy-cycle summaries |
| `FIRSTRADE_STRATEGY_PLUGIN_MOUNTS_JSON` | Optional | JSON sidecar plugin mount config. Overrides global `STRATEGY_PLUGIN_MOUNTS_JSON` for this platform |
| `CRISIS_ALERT_CHANNELS` | Optional | Crisis alert channel list: `email`, `sms`, `push`, and/or `telegram` |
| `CRISIS_ALERT_EMAIL_RECIPIENTS` | Optional | Email-form recipients. Use a normal mailbox for email-only delivery, or a Google Voice-associated mailbox/address to also trigger Google Voice prompts |
| `CRISIS_ALERT_EMAIL_SENDER_EMAIL` | Optional | Sender email address used for crisis alert email. Gmail is the default transport, but the sender naming is provider-neutral |
| `CRISIS_ALERT_EMAIL_SENDER_PASSWORD` | Optional | Sender SMTP password or app password, preferably supplied from Secret Manager in Cloud Run |
| `CRISIS_ALERT_EMAIL_SMTP_HOST` | Optional | SMTP host override. Defaults to Gmail SMTP when unset |
| `CRISIS_ALERT_EMAIL_SMTP_PORT` | Optional | SMTP port override. Defaults to `465` when unset |
| `CRISIS_ALERT_EMAIL_SMTP_SECURITY` | Optional | SMTP security override: `ssl`, `starttls`, or `none`. Defaults to `ssl` when unset |
| `CRISIS_ALERT_TELEGRAM_CHAT_IDS` | Optional | Dedicated crisis-alert Telegram chat IDs, separate from the strategy-cycle Telegram chat |
| `CRISIS_ALERT_TELEGRAM_BOT_TOKEN` | Optional | Dedicated crisis-alert Telegram bot token. Prefer `CRISIS_ALERT_TELEGRAM_BOT_TOKEN_SECRET_NAME` in env sync |
| `FIRSTRADE_COOKIE_DIR` | Optional | Cookie cache directory, default `.runtime/firstrade-cookies` |
| `FIRSTRADE_ENABLE_LIVE_TRADING` | Optional | Must be `true` before any live order can be submitted |
| `FIRSTRADE_RUN_SMOKE_ON_HTTP` | Optional | Must be `true` before `/smoke` performs a real login/quote |
| `FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP` | Optional | Must be `true` before `/session-check` performs a read-only login/session/account-state check |
| `FIRSTRADE_SESSION_CHECK_POLICY` | Optional | `/session-check` maintenance cadence: `auto`, `always`, or `skip`. Defaults to `auto`; monthly snapshot strategies run at most once per month when GCS state is available, while daily strategies run every check |
| `FIRSTRADE_SESSION_CHECK_INCLUDE_POSITIONS` | Optional | Include compact symbol/quantity/market-value positions in `/session-check` funds snapshots. Defaults to `false` |
| `FIRSTRADE_RUN_STRATEGY_ON_HTTP` | Optional | Must be `true` before `/run` performs strategy evaluation and order routing |
| `FIRSTRADE_LIVE_ORDER_ACK` | Optional | Must be `true` before `/run` can submit live orders |
| `FIRSTRADE_MAX_ORDER_NOTIONAL_USD` | Optional | Optional single-order cap for strategy-generated orders. Unset means no platform-side notional cap |
| `FIRSTRADE_MIN_RESERVED_CASH_USD` | Optional | Platform-level minimum cash reserve in USD. Defaults to `0`; the effective reserve is the max of this floor, `FIRSTRADE_RESERVED_CASH_RATIO * total equity`, and any strategy-provided reserve. |
| `FIRSTRADE_RESERVED_CASH_RATIO` | Optional | Platform-level minimum cash reserve ratio in `[0,1]`. Defaults to `0`; it can raise but not lower a strategy-provided reserve. |
| `FIRSTRADE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD` | Optional | Safe-haven/cash-sweep target values below this USD amount are kept as cash instead of buying BOXX/BIL. Default `1000`. |

## Local Validation

Install dependencies in a venv:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Quote-only smoke check:

```bash
.venv/bin/python scripts/firstrade_smoke_check.py --quote-only --symbol SPY
```

Read-only account payload check:

```bash
.venv/bin/python scripts/firstrade_smoke_check.py \
  --quote-only \
  --symbol SPY \
  --include-balances \
  --include-positions
```

The balance and position payloads are printed from the upstream package response.
Treat that output as sensitive account data.

Dry-run order preview for a tiny notional buy:

```bash
.venv/bin/python scripts/firstrade_smoke_check.py \
  --preview-order \
  --symbol YOUR_SYMBOL \
  --side buy \
  --notional-usd 5
```

Live order validation requires all of the following:

- `FIRSTRADE_ENABLE_LIVE_TRADING=true`
- `--live-order`
- `--yes-i-understand-unofficial-api-risk`
- order notional at or below `--max-notional-usd` when that optional cap is set

Example shape:

```bash
FIRSTRADE_ENABLE_LIVE_TRADING=true \
.venv/bin/python scripts/firstrade_smoke_check.py \
  --live-order \
  --symbol YOUR_SYMBOL \
  --side buy \
  --notional-usd 5 \
  --yes-i-understand-unofficial-api-risk
```

The example does not recommend any security. Choose the validation symbol
yourself and confirm Firstrade account permissions, fractional trading
agreement status, market session, and order preview before live use.

## Strategy Cycle

`/run` and `application.rebalance_service.run_strategy_cycle()` now perform a
full guarded strategy cycle:

- connect to Firstrade with the unofficial client
- read the selected account, balances, positions, quotes, and OHLC history
- load the selected shared `UsEquityStrategies` runtime
- load configured shared strategy plugin signal artifacts without changing core strategy logic
- map the strategy decision into a value-target Firstrade plan
- route generated orders through the local safety layer
- publish a compact Telegram summary when `TELEGRAM_TOKEN` and
  `GLOBAL_TELEGRAM_CHAT_ID` are configured
- send independent alerts for escalated strategy plugin signals through
  configured `CRISIS_ALERT_CHANNELS`
- write alert results into the response and suppress duplicate plugin
  alert keys through `STRATEGY_PLUGIN_ALERT_STATE_GCS_URI`, `EXECUTION_REPORT_GCS_URI`,
  or the configured Firstrade state bucket

The default mode remains dry-run. A live HTTP-triggered strategy order requires
all of these gates:

- `FIRSTRADE_RUN_STRATEGY_ON_HTTP=true`
- `FIRSTRADE_DRY_RUN_ONLY=false`
- `FIRSTRADE_ENABLE_LIVE_TRADING=true`
- `FIRSTRADE_LIVE_ORDER_ACK=true`
- order value at or below `FIRSTRADE_MAX_ORDER_NOTIONAL_USD` when that optional cap is set
- `FIRSTRADE_MIN_RESERVED_CASH_USD` / `FIRSTRADE_RESERVED_CASH_RATIO` may set a platform-level minimum cash reserve; defaults are `0`, and the effective reserve is the max of platform floor, platform ratio, and strategy reserve

The strategy execution service uses whole-share limit orders for generated
strategy orders. If the notional cap is below the current price of a target
symbol, that order is skipped instead of being enlarged.

For weight-target strategies, Firstrade translates weights into target values
using the account snapshot total equity. If a new or empty account reports
non-positive total equity, the runtime returns a `no_execute` value plan with
zero target values instead of attempting order translation.

`FIRSTRADE_REUSE_SESSION=true` reduces repeated login attempts by trying cached
session headers before calling Firstrade login again. By default this cache is
container-local. When `FIRSTRADE_PERSIST_SESSION_CACHE=true` and
`FIRSTRADE_GCS_STATE_BUCKET` is set, the same cache is also written to GCS so a
cold start can try the last known session first. Expired sessions, new broker
sessions from another device, or broker-side invalidation still fall back to a
fresh login.

`/session-check` is a read-only route for session keepalive experiments and
account-state persistence. With `FIRSTRADE_SESSION_CHECK_POLICY=auto`, it reads
the configured strategy cadence before connecting. Daily strategies and profiles
with daily canary checks run every time. Monthly snapshot strategies run once per
calendar month when `FIRSTRADE_GCS_STATE_BUCKET` is available; otherwise the
route runs conservatively instead of skipping. A skipped check returns
`session_check_skipped=true` and does not create a Firstrade client. When the
check runs, it connects to Firstrade, selects the account, reads balances,
optionally reads positions, and returns a compact masked funds snapshot. With
`FIRSTRADE_PERSIST_ACCOUNT_SNAPSHOT=true`, it writes the snapshot to
`accounts/<masked-account>/funds/latest.json` plus a timestamped history path
under the configured GCS prefix. Raw account IDs and login secrets are not
included in the snapshot.

When `FIRSTRADE_PERSIST_STRATEGY_RUNS=true` and a GCS state bucket is configured,
`/run` writes strategy state to
`strategy-runs/<masked-account>/<strategy-profile>/<yyyy-mm>/latest.json` plus a
timestamped history path. The record includes the planned targets, compact
portfolio snapshot, evaluation metadata, submitted orders, skipped orders, and
stage (`ORDERS_PLANNED`, `DRY_RUN_COMPLETED`, `NO_ACTION`, `SUBMITTED`,
`EXECUTION_BLOCKED`, `PARTIAL_SUBMITTED`, or `FUNDING_BLOCKED`). For live runs,
an existing terminal record in the same account/profile/month blocks duplicate
order submission. Terminal records include `SUBMITTED`, `FUNDING_BLOCKED`,
`RECONCILED`, and `COMPLETED`; transient execution blockers such as unavailable
quotes remain non-terminal so the scheduler can retry while the strategy's
trading-day execution window is still open. A pure insufficient-cash block is
recorded as `FUNDING_BLOCKED` with the skipped-order reason and is not retried
automatically for that period.


## GitHub-managed Cloud Run deploy and env sync

This repo includes `.github/workflows/sync-cloud-run-env.yml` for GitHub-managed
Cloud Run automation. Use these repository variables together when GitHub should
own the deployed runtime:

- `ENABLE_GITHUB_CLOUD_RUN_DEPLOY=true` to build, push, and deploy the Cloud Run image
- `ENABLE_GITHUB_ENV_SYNC=true` to sync runtime env vars to the Cloud Run service
- `ENABLE_MAIN_PUSH_CLOUD_RUN_AUTOMATION=true` to allow `main` pushes to run the
  deploy/env-sync workflow; manual `workflow_dispatch` runs do not require this flag

The main-push flag is an explicit automation ownership switch. Setting it to
`true` keeps the deployed US runtime aligned with the latest `main` version while
the live-order gates above still control whether `/run` can submit real orders.

## Runtime Guard Alerting

This repo also includes `.github/workflows/runtime-guard.yml`, a GitHub Actions
guard for failures that happen outside the Flask handler. It reads Cloud Logging
for recent Cloud Scheduler errors and Cloud Run request/runtime failures, then
sends Telegram directly through `CRISIS_ALERT_TELEGRAM_BOT_TOKEN` +
`CRISIS_ALERT_TELEGRAM_CHAT_IDS` or the fallback `TELEGRAM_TOKEN` +
`GLOBAL_TELEGRAM_CHAT_ID`.

The guard does not call `/run`, `/session-check`, or any trading endpoint. It is
a second notification layer for cases where Cloud Scheduler cannot reach Cloud
Run, OIDC/IAM/audience is wrong, Cloud Run returns 4xx/5xx, or the container
fails before the app-level Telegram fallback can run.

Required setup:

- keep `CLOUD_RUN_SERVICE` or `RUNTIME_GUARD_CLOUD_RUN_SERVICES` set to the
  deployed service name
- give the GitHub deploy service account `roles/logging.viewer` on the GCP
  project so it can read Cloud Logging
- keep Telegram chat/token variables or secrets configured in GitHub
- optionally set `RUNTIME_GUARD_SCHEDULER_JOB_PATTERN` to a regex that limits
  Scheduler log checks to this service's jobs

The scheduled guard checks every 30 minutes. To use it as a missed-run heartbeat,
set `RUNTIME_GUARD_REQUIRE_SUCCESS=true` and choose
`RUNTIME_GUARD_LOOKBACK_MINUTES` so the window covers the expected Firstrade
Scheduler run. The default leaves that heartbeat check off to avoid false alerts
outside trading windows.

`Execution Report Heartbeat` (`.github/workflows/execution-report-heartbeat.yml`)
is the stricter completion check. It runs on weekdays after the expected US
window and verifies that a recent strategy-run JSON exists under
`FIRSTRADE_GCS_STATE_BUCKET` / `FIRSTRADE_STATE_PREFIX`. It reads the latest
report status/stage and alerts if no recent report exists or the latest reports
are error-like. The deploy service account needs object read/list access on the
state bucket.

## Cloud Run Shape

`main.py` exposes:

- `/` health metadata only
- `/precheck` health metadata only
- `/probe` health metadata only
- `/profiles` shared US equity strategy matrix
- `/smoke` login + quote only when `FIRSTRADE_RUN_SMOKE_ON_HTTP=true`
- `/session-check` read-only session/account-state check only when
  `FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP=true`
- `/run` strategy evaluation + guarded order routing only when
  `FIRSTRADE_RUN_STRATEGY_ON_HTTP=true`

With the default environment, `/run` previews orders only. It can submit live
orders only when every live-trading gate above is enabled.

## Runtime State And Schedulers

The deployed Firstrade runtime keeps trading disabled unless the explicit live
order gates are changed:

- `FIRSTRADE_DRY_RUN_ONLY=true`
- `FIRSTRADE_RUN_STRATEGY_ON_HTTP=false`
- `FIRSTRADE_ENABLE_LIVE_TRADING=false`
- `FIRSTRADE_LIVE_ORDER_ACK=false`

For session keepalive tests, create a private GCS bucket, grant the Cloud Run
runtime service account object read/write access, and set:

- `FIRSTRADE_REUSE_SESSION=true`
- `FIRSTRADE_PERSIST_SESSION_CACHE=true`
- `FIRSTRADE_PERSIST_ACCOUNT_SNAPSHOT=true`
- `FIRSTRADE_PERSIST_STRATEGY_RUNS=true`
- `FIRSTRADE_GCS_STATE_BUCKET=<bucket-name>`
- `FIRSTRADE_STATE_PREFIX=firstrade-platform`
- `FIRSTRADE_RUN_SESSION_CHECK_ON_HTTP=true`
- `FIRSTRADE_SESSION_CHECK_POLICY=auto`

The `/session-check` scheduler can safely run more often than the strategy
scheduler because it is read-only. With the default `auto` policy, monthly
snapshot strategies only perform real session maintenance once per month after
the first successful check writes its maintenance marker to GCS. Daily strategies
still run every scheduler hit. The route logs `session_reused=true|false` for
real checks and `Firstrade session-check skipped` for cadence-based skips.

## License And Upstream Compliance

This repository is MIT licensed. The upstream `firstrade` package is also MIT
licensed. Keep `NOTICE.md` and upstream attribution when distributing this
project or derivative work.

Users are responsible for reviewing Firstrade account agreements, platform
terms, applicable law, and the upstream open-source license before using this
integration.
