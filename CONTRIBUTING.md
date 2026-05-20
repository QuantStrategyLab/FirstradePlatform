# Contributing

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
.venv/bin/python -m pytest -q
```

