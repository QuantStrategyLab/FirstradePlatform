# Contributing


## 中文摘要

- 用途：本文档围绕 `Contributing`，用于理解 `FirstradePlatform` 的配置、运行、部署、研究或验收边界。
- 主要覆盖：`Contributing`。
- 阅读顺序：先确认边界、输入输出和权限要求，再执行文档里的命令、CI、dry-run、发布或切换步骤。
- 风险提示：涉及实盘、密钥、权限、Cloud Run、交易所或券商 API 的变更，必须先在测试环境或 dry-run 验证；不要只凭示例直接修改生产。
- 英文正文保留更完整的命令、字段名和配置键；如果摘要和正文不一致，以正文中的实际命令和配置为准。
Keep this repository conservative. Firstrade support is based on an unofficial
reverse-engineered package, so changes should preserve local safety controls and
make failure modes obvious.

Rules for contributions:

- Do not remove the default dry-run behavior.
- Do not weaken the live order gates in `application/firstrade_client.py`.
- Do not add credentials, cookies, account IDs, balances, or raw order payloads
  to examples or tests.
- Keep upstream attribution in `NOTICE.md`.
- Prefer small tests that mock the broker boundary. Do not require live
  Firstrade credentials in CI.

Run the local unit tests before opening a pull request:

```bash
.venv/bin/python -m pip check
.venv/bin/ruff check --exclude external .
.venv/bin/python -m pytest -q
.venv/bin/python -m build
```
