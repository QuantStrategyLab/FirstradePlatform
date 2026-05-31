# Security Policy


## 中文摘要

- 用途：本文档围绕 `Security Policy`，用于理解 `FirstradePlatform` 的配置、运行、部署、研究或验收边界。
- 主要覆盖：`Security Policy`。
- 阅读顺序：先确认边界、输入输出和权限要求，再执行文档里的命令、CI、dry-run、发布或切换步骤。
- 风险提示：涉及实盘、密钥、权限、Cloud Run、交易所或券商 API 的变更，必须先在测试环境或 dry-run 验证；不要只凭示例直接修改生产。
- 英文正文保留更完整的命令、字段名和配置键；如果摘要和正文不一致，以正文中的实际命令和配置为准。
This repository integrates with an unofficial, reverse-engineered Firstrade API
client. Treat credentials, cookies, MFA secrets, and debug logs as highly
sensitive.

Do not commit:

- Firstrade username or password
- MFA secret, PIN, OTP codes, or recovery material
- `.runtime/` cookie files
- raw upstream HTTP request/response logs
- account numbers, balances, positions, or order confirmations

Report vulnerabilities privately through the repository security channel after
the repository is published. Until then, keep reports within the QuantStrategyLab
maintainer group.

