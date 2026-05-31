# Notices


## 中文摘要

- 用途：本文档围绕 `Notices`，用于理解 `FirstradePlatform` 的配置、运行、部署、研究或验收边界。
- 主要覆盖：`Notices`。
- 阅读顺序：先确认边界、输入输出和权限要求，再执行文档里的命令、CI、dry-run、发布或切换步骤。
- 风险提示：涉及实盘、密钥、权限、Cloud Run、交易所或券商 API 的变更，必须先在测试环境或 dry-run 验证；不要只凭示例直接修改生产。
- 英文正文保留更完整的命令、字段名和配置键；如果摘要和正文不一致，以正文中的实际命令和配置为准。
This repository integrates with the third-party `firstrade` Python package:

- Project: https://github.com/MaxxRK/firstrade-api
- Package: https://pypi.org/project/firstrade/
- License: MIT

`firstrade` is an unofficial, reverse-engineered Firstrade API client. It is
not affiliated with, endorsed by, or supported by Firstrade Securities Inc.

Keep this notice and the upstream license attribution when distributing this
repository or derivative work.

