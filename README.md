# Firstrade Platform

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
| `ACCOUNT_PREFIX` | Optional | Alert/log prefix, default `FIRSTRADE` |
| `ACCOUNT_REGION` | Optional | Runtime account scope, default `US` |
| `NOTIFY_LANG` | Optional | Notification language, `en` or `zh` |
| `TELEGRAM_TOKEN` | Optional | Telegram bot token for strategy-cycle summaries |
| `GLOBAL_TELEGRAM_CHAT_ID` | Optional | Telegram chat ID for strategy-cycle summaries |
| `FIRSTRADE_COOKIE_DIR` | Optional | Cookie cache directory, default `.runtime/firstrade-cookies` |
| `FIRSTRADE_ENABLE_LIVE_TRADING` | Optional | Must be `true` before any live order can be submitted |
| `FIRSTRADE_RUN_SMOKE_ON_HTTP` | Optional | Must be `true` before `/smoke` performs a real login/quote |
| `FIRSTRADE_RUN_STRATEGY_ON_HTTP` | Optional | Must be `true` before `/run` performs strategy evaluation and order routing |
| `FIRSTRADE_LIVE_ORDER_ACK` | Optional | Must be `true` before `/run` can submit live orders |
| `FIRSTRADE_MAX_ORDER_NOTIONAL_USD` | Optional | Single-order cap for strategy-generated orders, default `25` |
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
  --notional-usd 5 \
  --max-notional-usd 25
```

Live order validation requires all of the following:

- `FIRSTRADE_ENABLE_LIVE_TRADING=true`
- `--live-order`
- `--yes-i-understand-unofficial-api-risk`
- order notional at or below `--max-notional-usd`

Example shape:

```bash
FIRSTRADE_ENABLE_LIVE_TRADING=true \
.venv/bin/python scripts/firstrade_smoke_check.py \
  --live-order \
  --symbol YOUR_SYMBOL \
  --side buy \
  --notional-usd 5 \
  --max-notional-usd 25 \
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
- map the strategy decision into a value-target Firstrade plan
- route generated orders through the local safety layer
- publish a compact Telegram summary when `TELEGRAM_TOKEN` and
  `GLOBAL_TELEGRAM_CHAT_ID` are configured

The default mode remains dry-run. A live HTTP-triggered strategy order requires
all of these gates:

- `FIRSTRADE_RUN_STRATEGY_ON_HTTP=true`
- `FIRSTRADE_DRY_RUN_ONLY=false`
- `FIRSTRADE_ENABLE_LIVE_TRADING=true`
- `FIRSTRADE_LIVE_ORDER_ACK=true`
- order value at or below `FIRSTRADE_MAX_ORDER_NOTIONAL_USD`

The strategy execution service uses whole-share limit orders for generated
strategy orders. If the notional cap is below the current price of a target
symbol, that order is skipped instead of being enlarged.

## Cloud Run Shape

`main.py` exposes:

- `/` health metadata only
- `/precheck` health metadata only
- `/probe` health metadata only
- `/profiles` shared US equity strategy matrix
- `/smoke` login + quote only when `FIRSTRADE_RUN_SMOKE_ON_HTTP=true`
- `/run` strategy evaluation + guarded order routing only when
  `FIRSTRADE_RUN_STRATEGY_ON_HTTP=true`

With the default environment, `/run` previews orders only. It can submit live
orders only when every live-trading gate above is enabled.

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
- 金额不超过 `--max-notional-usd`

HTTP 策略闭环实盘还必须额外满足：

- `FIRSTRADE_RUN_STRATEGY_ON_HTTP=true`
- `FIRSTRADE_DRY_RUN_ONLY=false`
- `FIRSTRADE_LIVE_ORDER_ACK=true`
- 单笔金额不超过 `FIRSTRADE_MAX_ORDER_NOTIONAL_USD`
- `BOXX`/`BIL` 等避险现金替代标的目标金额低于 `FIRSTRADE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD` 时保留现金，默认门槛 `1000` USD

策略闭环生成的是整数股限价单。如果 `FIRSTRADE_MAX_ORDER_NOTIONAL_USD`
低于目标标的当前价格，本轮会跳过该订单，而不是放大金额。

请不要把 Firstrade 登录凭据、MFA secret、cookie 文件提交到 Git。`.env`、
`.runtime/` 和 `ft_cookies*.json` 已经在 `.gitignore` 中。

开源协议方面：本仓库使用 MIT；上游 `firstrade` 包也是 MIT。发布或二次分发
时保留 `NOTICE.md` 和上游项目信息。

`UsEquityStrategies` 已经内置 `firstrade` 平台 adapter。本仓库按 value-native
美股平台接入通用策略，策略逻辑不读取 Firstrade 环境变量，也不包含券商分支。
