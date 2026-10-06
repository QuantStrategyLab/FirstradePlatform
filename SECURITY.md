# Security Policy

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

