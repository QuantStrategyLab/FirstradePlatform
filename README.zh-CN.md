# FirstradePlatform

FirstradePlatform 是 QuantStrategyLab 内部一个实验性的 Firstrade 执行运行时。它读取其他 QSL 仓库发布的策略和 snapshot 产物，转换成 Firstrade 兼容的订单、通知和对账输出，让已经在别处完成研究的策略可以先 dry-run，之后再视情况接入真实 Firstrade 账户做实盘。底层 Firstrade API 客户端本身是非官方的逆向实现，因此这个平台更倾向保守的默认值和明确的 dry-run 限制，而不是追求使用便利。

[English README](README.md)

> 投资有风险。本项目不构成投资建议，仅用于学习、研究和工程审阅。

## 这个仓库是什么

FirstradePlatform 是 QuantStrategyLab 的实验性 Firstrade 执行平台。实验性接入 Firstrade，用于运行共享美股策略包。

它属于执行层，不是策略研究仓库。策略逻辑来自 `UsEquityStrategies`；如果 profile 依赖 snapshot，验证和产物来自 `UsEquitySnapshotPipelines`。

## QSL 架构角色

- **层级**：`执行平台`。
- **职责**：实验性 Firstrade 美股执行运行时。
- **事实源/归属**：Firstrade-compatible runtime 控制、生成订单、通知。
- **消费对象**：UsEquityStrategies、UsEquitySnapshotPipelines artifacts、QuantPlatformKit、QuantRuntimeSettings。
- **禁止事项**：无证据推广策略或把 secrets 写入 Git。

## 运行边界

- 只加载策略包暴露的 runtime-enabled profile。
- 负责券商/API 连接、dry-run 检查、通知和部署配置。
- 凭据必须放在 GitHub Secrets、云密钥系统或券商专用密钥系统中，不能提交到 Git。
- 任何 live 下单路径启用前，都应先从 dry-run 或 paper mode 开始。

## 实盘重试边界

运行时只会重试**从未向券商发出订单请求**的周期，例如暂时拿不到报价或可用现金不足。第一次
真实券商请求前会写入持久化、仅创建一次的提交锁；因此已被券商受理、拒绝、待处理、超时或结果
未知的请求都不会自动重发。资金不足只提醒一次，并会在受限的调度退避或策略窗口内的下一次运行时
再次检查。

## 普通 profile 与 snapshot-backed profile

普通 runtime profile 通常可以直接基于 market history 或 portfolio state 执行。Snapshot-backed profile 需要先从对应 snapshot pipeline 获取当前 artifact bundle，平台才应该执行。平台不应该自行判断策略资格，而应消费策略仓和 snapshot 仓发布的状态与产物。

## 安全部署顺序

1. 在 Git 之外配置 secrets 和 runtime variables。
2. 先以 dry-run 模式运行 workflow 或服务。
3. 检查生成订单、日志、通知和 reconciliation 输出。
4. 确认回滚步骤和 artifact 版本。
5. 上述检查清楚后，再启用定时任务或 live 执行。

## 只读账户资料

现有 Runtime Target Lifecycle 手动入口的 `metadata_only=true` 会核实际接收流量的修订，并只输出同步开关、固定目的地、绑定配置、token 引用与缓存配置是否存在。它不读取 Secret 内容或缓存，不调用券商；绑定准确性及缓存有效性明确标为尚未核验。配置存在不能代表余额已同步。

`POST /account-facts-sync` 是手动触发、仅读取缓存会话的账户快照入口。只有 `FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED=true` 时才开放；它不会登录、刷新凭据、自行定时、下单或发送通知。它只发布券商直接返回的净资产，以及存在时的 `cash_balance`；不会读取持仓，因此持仓读取失败或持仓资料不完整不会阻塞余额快照；也不会用 buying power 或持仓计算资产或现金。

Cloud Run 服务必须继续受 IAM 保护。调用方需要 `roles/run.invoker`，并把 Google 签名的 ID token 放在 `X-Serverless-Authorization`；独立的应用 token 放在 `Authorization: Bearer …`。参见 [Cloud Run 服务间认证文档](https://cloud.google.com/run/docs/authenticating/service-to-service)。这两种 token 都不是 Firstrade 凭据。

以下配置只能通过受保护的 Cloud Run 环境变量和 Secret Manager 引用配置：`FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED`、`FIRSTRADE_ACCOUNT_FACTS_SYNC_URL`（必须精确为 `https://qsl-strategy-switch-console.pigbibi.workers.dev/api/account-facts/sync`）、`FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN`（专用 secret）、`FIRSTRADE_ACCOUNT_FACTS_TARGET_ID`、`FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID`、`FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_KEY` 和 `FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE`。账户身份复用受保护 runtime target 中唯一且精确的 selector；若同时设置 `FIRSTRADE_ACCOUNT`，它必须完全匹配。所有值都必须与 QRS 可信绑定及当前 runtime target 一致；不要推导或编造 binding ID。

现有手动 Cloud Run 环境同步会从 `CLOUD_RUN_SERVICE_TARGETS_JSON` 中所选 Firstrade target 的 `env` 配置传递六个非 token 项；单服务旧配置路径也可使用匹配的 `FIRSTRADE_ACCOUNT_FACTS_*` 输入。target、binding、account key 和 scope 放在受保护的 GitHub 配置中。受保护的 GitHub variable `FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN_SECRET_NAME` 只填写已批准 Secret Manager secret 的名称；workflow 只把这个 Secret Manager 引用映射到 `FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN`，不会接受 GitHub Actions 传入的明文 token。部署另行审查和采用前，启用开关应保持未设置或 `false`。本次源码修改没有应用 Cloud Run 配置，也不代表部署已采用。

现有 `sync-cloud-run-env.yml` 还提供独立且默认关闭的 `sync_account_facts_configuration` 手动输入。它会先核验受保护 runtime target、源码 SHA、批准的分支引用和专用 Secret 引用，再只对当前已就绪 revision 增量更新六个 `FIRSTRADE_ACCOUNT_FACTS_*` 环境值及已有的 token Secret Manager 引用。部署身份不读取 Secret 版本元数据或内容；Cloud Run 使用 runtime 身份检查 Secret 挂载，新 revision 的 Ready 与源码 SHA 检查仍须通过。这些部署检查不表示账户绑定或缓存会话已获验证。此模式不能与广义配置同步、流量提升、清理或诊断 staging 同时使用；它使用 `--no-traffic`，并核对其他配置和当前流量未变化。它不会创建 secret 或 IAM 绑定。成功只表示配置暂存到零流量 revision；服务流量采用和严格余额同步仍需分别完成。

零流量候选的 `latestCreatedRevisionName` 可以与服务的 `latestReadyRevisionName` 不同；服务别名不能代替候选本身的就绪状态。配置同步前后都会读取精确 `latestCreatedRevisionName` 对应的 revision，要求其自身 `Ready=True` 且源码 SHA 匹配。别名不同不会触发流量提升，也不会放宽其他配置或流量读回校验。模板比较只额外忽略 gcloud 生成的 `client.knative.dev/nonce` label，并归一化空 labels；源码及其他 labels、annotations、非 facts 环境变量、镜像、运行身份、探针、资源和流量仍须保持。

仅供说明的合成配置：

```text
FIRSTRADE_ACCOUNT_FACTS_SYNC_ENABLED=false
FIRSTRADE_ACCOUNT_FACTS_TARGET_ID=synthetic-target
FIRSTRADE_ACCOUNT_FACTS_SOURCE_BINDING_ID=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_KEY=synthetic-account-key
FIRSTRADE_ACCOUNT_FACTS_ACCOUNT_SCOPE=US
FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN_SECRET_NAME=<已批准的secret名称>
FIRSTRADE_ACCOUNT=synthetic-native-account-id  # 可选；设置后必须匹配 runtime target
# FIRSTRADE_ACCOUNT_FACTS_SYNC_TOKEN 仅由受保护的环境同步 workflow
# 通过 Secret Manager 引用注入。
```

只有在接收端账户绑定和独立同步 token 配置并核验后，才可开启同步。接口只返回固定状态或错误码，不返回原生账户 ID。

## 仓库结构

- `tests/`：单元测试、契约测试和回归测试。
- `.github/workflows/`：CI、定时任务、发布或部署 workflow。
- `scripts/`：运维脚本和本地辅助工具。

## 快速开始

```bash
uv sync --frozen --extra test
uv run --no-sync ruff check --exclude external .
uv run --no-sync python scripts/check_qpk_pin_consistency.py
```

## 延伸文档

- 暂无独立 `docs/` 目录；请先阅读本 README 和 workflow 文件。

## 社区和安全

- 贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，确认 PR 范围、本地校验和文档要求。
- 讨论、issue 和 review 请遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
- 涉及密钥、自动化、券商/交易所或云资源的漏洞请按 [SECURITY.md](SECURITY.md) 私密报告；不要为 secret 或实盘风险开公开 issue。

## 许可证

详见 [LICENSE](LICENSE)。
