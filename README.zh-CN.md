# FirstradePlatform

[English README](README.md)

> ⚠️ 投资有风险，不构成投资建议，仅供学习交流用途。

## 这个项目做什么

FirstradePlatform 是 QuantStrategyLab 体系中的**实验性执行平台**。面向 QuantStrategyLab 美股策略的实验性 Firstrade 运行平台。

## 适合谁使用

- 希望阅读、复现或扩展 QuantStrategyLab 相关模块的工程师和研究人员。
- 在阅读详细 runbook 或 workflow 前，需要先理解项目入口的运维人员。
- 在启用自动化前，需要确认项目职责、安全边界和证据要求的 reviewer。

## 当前状态

实验性平台；未经额外验证，不应视为可生产运行。

## 仓库结构

- `application/`, `notifications/`：Python 包代码。
- `tests/`：单元测试和契约测试。
- `.github/workflows/`：CI、定时任务和部署 workflow。
- `scripts/`：运维脚本和本地辅助工具。

## 快速开始

从全新 clone 开始：

```bash
python -m pip install -e .
python -m pytest -q
```

如果命令需要凭据，请先阅读相关 workflow 或 runbook，并把密钥配置在 Git 之外。

## 部署和运行

先使用隔离凭据和 dry-run 或 paper 类检查。确认认证、订单生成和券商响应后，再扩大范围。

建议先手工运行或 dry-run。只有在日志、产物、权限和回滚步骤都检查过之后，才启用定时任务或 live 执行。

## 策略表现与证据边界

执行实验不能证明策略表现。策略收益、回撤和基准检查应以 UsEquityStrategies 和快照证据为准。

README 不应该承诺固定收益或过期指标。实际使用前，请重新运行对应测试、回测或流水线任务。

## 安全注意事项

- 不要把 API key、券商凭据、OAuth token、Cookie 或账户标识提交到 Git。
- 新策略或平台变更在 live 前必须先跑 dry-run 或 paper 流程。
- 启用定时任务前，需要人工检查生成的订单、产物和日志。

## 参与贡献

请保持改动小、可复现，并用最小必要测试覆盖。涉及策略的改动，需要附上验证行为的证据产物或命令。

## 许可证

如仓库包含 [LICENSE](LICENSE)，请以该文件为准。
