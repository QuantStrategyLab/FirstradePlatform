# Firstrade Platform

> ⚠️ 投资有风险，不构成投资建议，仅供学习交流用途。

语言: [中文](README.zh-CN.md) | [English](README.md)

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

weight-target 策略会用账户快照里的 total equity 换算成目标金额。如果新账户或空账户
返回的 total equity 非正数，运行时会生成带 `no_execute` 标记、目标金额为 0 的
value-mode 计划，而不是继续进入订单翻译并抛出校验错误。

`FIRSTRADE_REUSE_SESSION=true` 会优先复用缓存的 Firstrade session header，减少重复
登录。默认缓存只在容器本地；如果同时设置 `FIRSTRADE_PERSIST_SESSION_CACHE=true`
和 `FIRSTRADE_GCS_STATE_BUCKET`，缓存会写入 GCS，冷启动时也能先尝试复用。缓存过期、
其他设备登录或券商侧失效时，仍会回退到重新登录。

`/session-check` 是只读的会话维护和账户状态检查入口。设置
`FIRSTRADE_SESSION_CHECK_POLICY=auto` 时，它会先读取当前策略 cadence，再决定是否连接
Firstrade。日频策略和带 daily canary 的策略每次都检查；月度 snapshot 策略在配置
`FIRSTRADE_GCS_STATE_BUCKET` 后每个自然月只做一次真实维护。没有 GCS state 或策略上下文
不可用时，系统会保守执行而不是跳过。被跳过的检查会返回
`session_check_skipped=true`，并且不会创建 Firstrade client。

启用 `FIRSTRADE_PERSIST_STRATEGY_RUNS=true` 且配置 GCS state bucket 后，`/run` 会把策略
运行状态写入 `strategy-runs/<masked-account>/<strategy-profile>/<yyyy-mm>/latest.json`，
并写入带时间戳的历史路径。记录包含目标计划、脱敏后的组合快照、评估元数据、已提交订单、
跳过订单和 stage。常见 stage 包括 `ORDERS_PLANNED`、`DRY_RUN_COMPLETED`、`NO_ACTION`、
`SUBMITTED`、`EXECUTION_BLOCKED`、`PARTIAL_SUBMITTED` 和 `FUNDING_BLOCKED`。实盘运行中，
同一账户、同一 profile、同一月份已有终态记录时，会阻止重复提交订单。终态包括
`SUBMITTED`、`FUNDING_BLOCKED`、`RECONCILED` 和 `COMPLETED`。报价不可用这类临时执行阻塞会
保持非终态，scheduler 可在策略交易日执行窗口内重试；如果纯粹是现金不足以买入一整股，
则记录为 `FUNDING_BLOCKED`，通知和日志会带跳过原因，同一周期不会自动重复重试。


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
