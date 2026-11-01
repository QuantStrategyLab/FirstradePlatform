# Statement reconciliation: fee split and valuation basis

Two reconciliation gaps looked unexplained but have fixed, documented causes.
`application/statement_reconciliation.py` models them as pure, offline helpers.
Nothing in it calls a broker, reads credentials, or writes data.

## Desk-executed sells: broker-assisted fee plus regulatory fee

A sell whose description contains `BROKER/DEALER ORDER` (this includes
`MARGIN SELLOUT`) nets less than `quantity x price`:

```
expected net = quantity x price - broker-assisted fee - regulatory fee
```

- Broker-assisted fee: `$19.95` per order (Firstrade pricing page).
- Regulatory fee: SEC Section 31 (`rate per $1M x proceeds`) plus FINRA TAF
  (`rate per share`, capped per trade). The unrounded amounts are summed,
  rounded up to the next cent, with a `$0.01` minimum whenever any fee is owed.
- A residual within `+/- $0.01` after this split is reported as `explained`.
  Larger residuals are `unexplained`. A trade date outside the configured
  schedule is `unsupported` (fail closed), never guessed.

Worked case, 2026-08-28 IBIT: `2 x 43.7724 = 87.5448`, minus `19.95` and
`0.01` gives `67.58`, which matches the reported amount. The earlier
"unexplained `19.96`" is the sum of the two fees.

Rates live in `application/firstrade_fee_schedule.py` as effective-dated
segments (SEC `0.00` from 2025-05-14, `20.60` per million from 2026-04-04;
TAF `0.000195` per share in 2026, paused 2026-10-01 to 2026-12-31). Update the
segments when SEC or FINRA publish a new rate; `load_fee_schedule()` validates
ordering and values.

## Valuation basis difference

The balances endpoint and the positions endpoint price the same shares from
different quote bases, so `long_stock_value` (balances) and the sum of
position `market_value` can differ by a few cents with identical quantities and
cash. `valuation_basis_difference` reports this as its own status:

| Status | Meaning |
| --- | --- |
| `match` | Exactly equal. |
| `valuation_basis_difference` | Non-zero, within `max($0.10, 0.1% of market value)`. |
| `unexplained` | Larger than the tolerance. |
| `unavailable` | An input was missing. |

Worked case, 2026-10-31: balance `130.56` against positions `130.6232` is a
`0.0632` gap, within the `0.1306` tolerance.

The read-only collector now keeps `long_stock_value` as a distinct balance fact
(`balances.long_stock_value`, coverage flag `long_stock_value_available`).
`build_firstrade_account_snapshot(..., include_valuation_basis=True)` can persist
it next to `net_assets`. This is off by default because the QRS console
currently accepts exactly `currency` and `net_assets` in Firstrade balance rows
and rejects additional keys; extend that schema before enabling it.
