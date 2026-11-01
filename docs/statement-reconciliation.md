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
- Quantity and price must both be greater than zero; otherwise the row is
  `unsupported` (`quantity_not_positive` / `price_not_positive`).
- The CSV `Date` column is the **trade date**, not the settlement date. In the
  2026-08-28 IBIT row `Date` is `8/28/2026`, the description has
  `EXEC TIME: 2026-08-28 ...` and `S/D: 08/31/2026` (T+1). SEC Section 31 and
  FINRA TAF are charged on the trade date, so rates are selected by `Date` and
  `settlement_date` is only reported. A row whose `Date` disagrees with
  `EXEC TIME`, or is not before `S/D`, is `unsupported`
  (`date_column_not_trade_date`).

Worked case, 2026-08-28 IBIT: `2 x 43.7724 = 87.5448`, minus `19.95` and
`0.01` gives `67.58`, which matches the reported amount. The earlier
"unexplained `19.96`" is the sum of the two fees.

Rates live in `application/firstrade_fee_schedule.py` as effective-dated
segments (SEC `0.00` from 2025-05-14, `20.60` per million from 2026-04-04).
TAF for covered equities: `0.000195` per share (max `$9.79`) in 2026, paused
2026-10-01 to 2026-12-31 (SR-FINRA-2026-021), then `0.000195` / `$9.79` again
for 2027 and 2028 because SR-FINRA-2026-020 postpones the remaining increases
by two years: `0.000232` / `$11.61` from 2029-01-01, `0.000240` / `$12.05` from
2030-01-01 and `0.000249` / `$12.50` from 2031-01-01. Update the segments when
SEC or FINRA publish a new rate; `load_fee_schedule()` validates ordering and
values.

`verified_through` (currently `2026-12-01`) is the last trade date the rates
were checked against the published sources. Trade dates after it are
`unsupported` (`..._not_verified_after_<date>`), because the SEC fiscal year
2027 Section 31 rate has not been published (the FY2026 rate stays in effect
until 60 days after the FY2027 appropriation is enacted) and the FINRA filings
above are immediately effective but still open to SEC suspension. Re-verify the
sources and move `verified_through` forward in the same change.

The `Fee schedule check` workflow (`.github/workflows/fee-schedule-check.yml`) does
this review every month, on the 22nd at 02:17 UTC, and on demand through
`workflow_dispatch`. `scripts/check_fee_schedule.py check` reads the SEC Section 31
advisories and SEC filing pages, and the FINRA fee adjustment schedule, rule filing
listing and filing PDFs, then compares them with the schedule. A fetch or parse
failure is an error, never a pass.

- Everything matches: the `bump` job moves `verified_through` to the end of the next
  month (plus the "checked" date in the module docstring, the test review pin
  `VERIFIED_THROUGH` and the two doc mentions), runs the tests and opens a pull
  request. It never merges.
- A rate changed, a new FINRA fee filing appeared, an SEC page mentions a
  suspension, or the check failed: the `notify` job opens or updates one issue
  titled "Firstrade fee schedule check needs attention". Rates are never edited
  automatically; update the segments in a normal pull request.

The `bump` job pushes its branch and opens the pull request with a short-lived
installation token of the GitHub App `qsl-qpk-pin-sync` (ID 5004823), so CI starts on the
pull request like on any other. The App is already installed on this repository with
`contents: write` and `pull-requests: write`. The token is created with
`actions/create-github-app-token`, scoped to this repository only, and limited to
`contents: write` and `pull-requests: write`. The repository setting that lets Actions
create pull requests stays off. The setup is repository level:

- the repository variable `FIRSTRADE_PR_APP_ID` (the App ID);
- the repository secret `FIRSTRADE_PR_APP_PRIVATE_KEY` (the App private key).

Without the variable or secret, or when the token cannot be created, the job falls back to
`GITHUB_TOKEN`: it leaves the branch on the remote and the issue carries a compare link
instead; a pull request opened by hand from it needs CI to be run by closing and
reopening it.

Set the repository variable `FEE_CHECK_CONTACT` to a monitored address: sec.gov rejects
clients that do not identify themselves. The script is tied to the current page
layouts and to the filings named in `REVIEWED_FINRA_FILINGS`; when a layout
changes the check fails loudly and the parser needs an update.

## Valuation basis difference

The balances endpoint and the positions endpoint price the same shares from
different quote bases, so `long_stock_value` (balances) and the sum of
position `market_value` can differ by a few cents with identical quantities and
cash. `valuation_basis_difference` reports this as its own status:

| Status | Meaning |
| --- | --- |
| `match` | Exactly equal. |
| `valuation_basis_difference` | Non-zero, within `min(max($0.10, 0.1% of market value), $25.00)`. |
| `unexplained` | Larger than the tolerance, a position quantity mismatch, or an empty positions list with a non-zero balance value. |
| `unavailable` | An input was missing or negative. |

The `$25.00` absolute cap keeps 0.1% of a large account from hiding a real
break. Pass `expected_quantities` (for example from the previous snapshot or
the ledger) to cross-check share counts: a price-basis gap is only credible when
quantities agree, so any quantity difference is `unexplained`
(`quantity_mismatch`). `quantity_check` reports `matched`, `mismatch` or
`not_performed`; an empty positions list only matches a zero balance value.

Worked case, 2026-10-31: balance `130.56` against positions `130.6232` is a
`0.0632` gap, within the `0.1306` tolerance.

The read-only collector now keeps `long_stock_value` as a distinct balance fact
(`balances.long_stock_value`, coverage flags `long_stock_value_available` and
`long_stock_value_ambiguous`). If the top-level and `result` values conflict, the
value is withheld and flagged ambiguous; the rest of the collection (cash,
equity, positions) is still returned.
`build_firstrade_account_snapshot(..., include_valuation_basis=True)` can persist
it next to `net_assets`. This is off by default because the QRS console
currently accepts exactly `currency` and `net_assets` in Firstrade balance rows
and rejects additional keys; extend that schema before enabling it.
