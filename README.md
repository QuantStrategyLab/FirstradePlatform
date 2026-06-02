# Firstrade Platform

> ⚠️ 投资有风险，不构成投资建议，仅供学习交流用途。

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

`FIRSTRADE_REUSE_SESSION=true` reduces repeated login attempts by trying cached
session headers before calling Firstrade login again. By default this cache is
container-local. When `FIRSTRADE_PERSIST_SESSION_CACHE=true` and
`FIRSTRADE_GCS_STATE_BUCKET` is set, the same cache is also written to GCS so a
cold start can try the last known session first. Expired sessions, new broker
sessions from another device, or broker-side invalidation still fall back to a
fresh login.

`/session-check` is a read-only route for session keepalive experiments and
account-state persistence. It connects to Firstrade, selects the account, reads
balances, optionally reads positions, and returns a compact masked funds
snapshot. With `FIRSTRADE_PERSIST_ACCOUNT_SNAPSHOT=true`, it writes the snapshot
to `accounts/<masked-account>/funds/latest.json` plus a timestamped history path
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

中文说明：启用 `FIRSTRADE_PERSIST_STRATEGY_RUNS=true` 且配置 GCS state bucket
后，`/run` 会把策略运行状态写入
`strategy-runs/<masked-account>/<strategy-profile>/<yyyy-mm>/latest.json`，
并写入带时间戳的历史路径。记录包含目标计划、脱敏后的组合快照、评估元数据、
已提交订单、跳过订单和 stage。常见 stage 包括 `ORDERS_PLANNED`、
`DRY_RUN_COMPLETED`、`NO_ACTION`、`SUBMITTED`、`EXECUTION_BLOCKED`、
`PARTIAL_SUBMITTED` 和 `FUNDING_BLOCKED`。实盘运行中，同一账户、同一
profile、同一月份已有终态记录时，会阻止重复提交订单。终态包括 `SUBMITTED`、
`FUNDING_BLOCKED`、`RECONCILED` 和 `COMPLETED`。例如报价不可用这类临时
执行阻塞会保持非终态，scheduler 可在策略交易日执行窗口内重试；如果纯粹是现金
不足以买入一整股，则记录为 `FUNDING_BLOCKED`，通知和日志会带跳过原因，
同一周期不会自动重复重试。


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

The `/session-check` scheduler can safely run more often than the strategy
scheduler because it is read-only. A typical test schedule is every 30 minutes
during US regular market hours. The route logs `session_reused=true|false` and
writes the latest masked funds snapshot plus timestamped history to GCS.

## License And Upstream Compliance

This repository is MIT licensed. The upstream `firstrade` package is also MIT
licensed. Keep `NOTICE.md` and upstream attribution when distributing this
project or derivative work.

Users are responsible for reviewing Firstrade account agreements, platform
terms, applicable law, and the upstream open-source license before using this
integration.

---

## 中文说明

这是一个 QuantStrategyLab 风格的 Firstrade 平台层仓库。它接入的是
`firstrade` 这个非官方、逆向工程 Python 包，不是 Firstrade 官方 API。

当前目标是对齐 `InteractiveBrokersPlatform`、`CharlesSchwabPlatform` 和
`LongBridgePlatform`：策略逻辑放在 `UsEquityStrategies`，这个仓库只负责
Firstrade 登录、账户/行情读取、下单转换、安全闸和部署 wiring。

当前定位是小规模验证到通用美股平台层的过渡：

- 登录和 MFA 验证
- 账户、持仓、行情、OHLC 读取
- dry-run / preview 下单验证
- `/run` 执行通用美股策略的 dry-run 调仓闭环
- 配置 `TELEGRAM_TOKEN` 和 `GLOBAL_TELEGRAM_CHAT_ID` 后发送运行摘要
- 读取通用策略插件信号，并在危机类插件触发时按 `CRISIS_ALERT_CHANNELS`
  配置发送独立告警
- 在响应中写入告警结果，并通过 `STRATEGY_PLUGIN_ALERT_STATE_GCS_URI`、
  `EXECUTION_REPORT_GCS_URI` 或已配置的 Firstrade state bucket 抑制重复插件告警 key
- 在你再次确认后，才允许极小金额实盘验证
- 通用 `us_equity` 策略 profile 的平台层接入

可以用只读 smoke 命令读取余额和持仓：

```bash
.venv/bin/python scripts/firstrade_smoke_check.py \
  --quote-only \
  --symbol SPY \
  --include-balances \
  --include-positions
```

该输出包含账户敏感信息，不要贴到公开 issue、日志或 PR。

默认所有订单都是 preview。CLI 实盘必须同时满足：

- 设置 `FIRSTRADE_ENABLE_LIVE_TRADING=true`
- CLI 使用 `--live-order`
- CLI 使用 `--yes-i-understand-unofficial-api-risk`
- 如果设置了 `--max-notional-usd`，金额不超过该上限

HTTP 策略闭环实盘还必须额外满足：

- `FIRSTRADE_RUN_STRATEGY_ON_HTTP=true`
- `FIRSTRADE_DRY_RUN_ONLY=false`
- `FIRSTRADE_LIVE_ORDER_ACK=true`
- 如果设置了 `FIRSTRADE_MAX_ORDER_NOTIONAL_USD`，单笔金额不超过该上限
- `FIRSTRADE_MIN_RESERVED_CASH_USD` / `FIRSTRADE_RESERVED_CASH_RATIO` 可设置平台级最低预留现金；默认都是 `0`，实际预留取平台下限、平台比例和策略预留中的最大值
- `BOXX`/`BIL` 等避险现金替代标的目标金额低于 `FIRSTRADE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD` 时保留现金，默认门槛 `1000` USD

策略闭环生成的是整数股限价单。如果设置了 `FIRSTRADE_MAX_ORDER_NOTIONAL_USD`
且它低于目标标的当前价格，本轮会跳过该订单，而不是放大金额。


### GitHub 统一管理 Cloud Run 部署和环境变量

这个仓库提供 `.github/workflows/sync-cloud-run-env.yml` 作为 GitHub 管理
Cloud Run 的入口。如果希望 GitHub 接管已部署运行时，仓库级 Variables 建议一起设置：

- `ENABLE_GITHUB_CLOUD_RUN_DEPLOY=true`：构建、推送并部署 Cloud Run 镜像
- `ENABLE_GITHUB_ENV_SYNC=true`：把运行时环境变量同步到 Cloud Run 服务
- `ENABLE_MAIN_PUSH_CLOUD_RUN_AUTOMATION=true`：允许 `main` push 触发
  deploy/env-sync workflow；手动 `workflow_dispatch` 不要求这个开关

`ENABLE_MAIN_PUSH_CLOUD_RUN_AUTOMATION` 是显式的主分支发布所有权开关。设为
`true` 后，美股运行时会跟随最新 `main` 部署；是否允许 `/run` 提交真实订单仍由上面的
live-order 安全闸控制。

### Runtime Guard 告警

仓库还提供 `.github/workflows/runtime-guard.yml`。这个 workflow 不会调用
`/run`、`/session-check` 或任何交易入口，只读取 Cloud Logging 中最近的 Cloud
Scheduler 错误和 Cloud Run 请求/运行失败，并直接用
`CRISIS_ALERT_TELEGRAM_BOT_TOKEN` + `CRISIS_ALERT_TELEGRAM_CHAT_IDS` 或 fallback
的 `TELEGRAM_TOKEN` + `GLOBAL_TELEGRAM_CHAT_ID` 发 Telegram。

这层保护覆盖 Flask handler 还没来得及发通知的场景，例如 Scheduler 没打到 Cloud
Run、OIDC/IAM/audience 配错、Cloud Run 返回 4xx/5xx，或容器启动/导入阶段已经失败。

需要的配置：

- `CLOUD_RUN_SERVICE` 或 `RUNTIME_GUARD_CLOUD_RUN_SERVICES` 指向已部署服务
- GitHub deploy service account 需要项目级 `roles/logging.viewer`，用于读取 Cloud Logging
- GitHub 中继续配置 Telegram chat/token 变量或 secrets
- 可选设置 `RUNTIME_GUARD_SCHEDULER_JOB_PATTERN`，用正则把 Scheduler 日志限制到本服务的 job

默认计划每 30 分钟检查一次。若要把它作为 missed-run 心跳检查，设置
`RUNTIME_GUARD_REQUIRE_SUCCESS=true`，并把 `RUNTIME_GUARD_LOOKBACK_MINUTES` 设成覆盖
Firstrade 预期 Scheduler 运行时间的窗口。默认不强制心跳，避免非交易窗口误报。

更严格的完成检查是 `Execution Report Heartbeat`
（`.github/workflows/execution-report-heartbeat.yml`）。它会在工作日美股预期窗口后检查
`FIRSTRADE_GCS_STATE_BUCKET` / `FIRSTRADE_STATE_PREFIX` 下最近的 strategy-run JSON，
读取 `status/stage/errors`，如果没有近期 report 或 report 呈错误状态就发 Telegram。
GitHub deploy service account 需要对 state bucket 有对象读取/列举权限。

请不要把 Firstrade 登录凭据、MFA secret、cookie 文件提交到 Git。`.env`、
`.runtime/` 和 `ft_cookies*.json` 已经在 `.gitignore` 中。

开源协议方面：本仓库使用 MIT；上游 `firstrade` 包也是 MIT。发布或二次分发
时保留 `NOTICE.md` 和上游项目信息。

`UsEquityStrategies` 已经内置 `firstrade` 平台 adapter。本仓库按 value-native
美股平台接入通用策略，策略逻辑不读取 Firstrade 环境变量，也不包含券商分支。
