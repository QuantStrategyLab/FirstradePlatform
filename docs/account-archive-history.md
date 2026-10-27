# Saved account history

`Publish saved account history day` previews one existing UTC day by default.
It reads private funds archives in the cloud runner with the existing deployment
identity. It never authenticates to Firstrade, invokes a broker service, or
creates a schedule. Choose `publish=true` only for an approved missing day after
checking the console's identity projection for an existing row.

Cloud project, WIF provider and existing deployment service-account coordinates
are supplied through `ACCOUNT_HISTORY_GCP_PROJECT_ID`,
`ACCOUNT_HISTORY_GCP_WIF_PROVIDER` and `ACCOUNT_HISTORY_GCP_SERVICE_ACCOUNT`
repository variables. They must identify the already approved deployment target;
this workflow creates no identity or permission. Service/region and account
binding settings use the existing repository configuration. Publishing reads only
the dedicated sync token of the actual serving revision, from its existing
configuration or Secret reference, in cloud-runner memory. Preview does not read
that token.

The selected archive must match the current runtime's exact native account,
storage namespace and original observation date. Its source binding is checked
against the existing configured service, deployment selector, scope and account.
Ambiguous or incomplete listings fail before publishing. Financial bodies stay
in runner memory; output contains only dates, counts and verification flags.

Only the saved `account_list_total_value`/account-level total and exact
`result.cash_balance`/`cash_balance` fields are projected. Original decimal text
and observation time are preserved. USD follows the existing Firstrade US-account
currency policy. Positions, cash-balance changes, buying power and margin values
are not converted into historical positions, external flows or returns.

Publishing uses the existing dedicated platform sync token and the console's
`/api/account-facts/history/sync` route. This route only appends daily history:
latest observations, source trust, account identity and external flows remain
unchanged. Existing conflicting records are rejected; unknown POST outcomes are
not automatically retried. A successful exact history-only ACK is not a logged-in
API or page acceptance result.

## Cached account-history source inspection

`Inspect cached account history schema` reads one first page for the current
serving runtime's exact account and at most 31 closed UTC dates within the past
year. It uses the same configured deployment identity, the existing username
Secret and saved authentication tokens in cloud-runner memory. Password and MFA
credentials are never loaded. The original runtime cache TTL is preserved;
missing, expired or rejected authentication stops without login or cache writes.
A storage read failure is reported separately from a stale cache.
Cloud read failures expose a closed error category and stage, never the original
CLI error, resource path or credential. Permission failures do not trigger a
different credential source, identity or an automatic retry.

After cached user-info/account-list validation, the explicit client method arms
exactly one history GET with a fixed account, date range, first page and row
limit. The ordinary cached-only transport still rejects history URLs. Redirects,
extra request parameters, retries and subsequent history calls are blocked.
Only fixed field labels, types, container counts and bounded integer pagination
counts leave the runner. Missing or invalid counts remain null; an explicit
provider error stops inspection without exporting its text. Pagination counts
describe the returned first-page metadata and do not certify source coverage. A first
page is not complete history, a missing container is not an empty result, and
field presence alone does not qualify a transaction as an external flow or fill.
The fixed `report_date` label follows the upstream SDK's history example in
[`test.py`](https://github.com/MaxxRK/firstrade-api/blob/main/test.py); it is not
treated as an execution or settlement date merely because it is present.
The fixed `trade_price`, `trans_str` and `descriptionArray` labels are also used
by public native-history consumers ([history renderer](https://github.com/changtimwu/ftapi-skill/blob/f0ab081a9dd27339b9a798549b0c5720de3a3c3c/history.py)
and [ledger parser](https://github.com/PatrickSUDO/fadacai-portfolio/blob/85bfdff39e91ea013ff0e06e1695db4d90fee3fc/tools/trade_ledger.py)).
These references justify inspecting those exact labels, not adopting the callers'
accounting classifications, numeric conversions or authentication behaviour.
This inspection does not publish account facts, returns, flows or executions,
deploy an image, change permissions, notify, or create a schedule.

The optional manual `archive_page=true` input additionally preserves this one
page in the serving runtime's existing private state bucket. It checks enforced
public-access prevention and uniform bucket access first, using the same existing
deployment identity. The archive contains the exact response-body bytes encoded
as base64, their SHA-256, the requested account fingerprint/window/page, collector
source and retrieval time. Retrieval time is not a transaction or valuation date.
The run-specific object is created with `ifGenerationMatch=0` and read back at
the returned generation before reporting success. Collisions, uncertain writes
and failed readback stop without retries, overwrites or deletion. No object path,
response body, dynamic field name or financial value is printed or uploaded to
GitHub artifacts. The manifest always marks coverage as first-page-only and
transactions as unqualified; archiving does not certify a complete window or
publish flows/fills. Ordinary schema-only inspection remains read-only.

To inspect an already verified page without another broker request, provide all
three `archive_run`, `archive_generation` and `archive_sha256` inputs from its
receipt and keep `archive_page=false`. The object path is constructed from the
current runtime account and exact requested window; arbitrary paths are not
accepted. The reader verifies the pinned object's hash, manifest account/window
and native-body hash in cloud memory. It never reads the username Secret or
session cache, contacts the broker, writes an object or publishes business data.
The fixed-label schema projection now also includes `semantic_checks` for archived
pages. It counts parsed, invalid, unsupported and ambiguous `report_date` values
and dates inside/outside the requested window, without returning the original
strings. Exact ISO dates, compact year-first dates and year-first slash dates are
recognized. A numeric slash date is parsed only when its valid month-first and
day-first interpretations yield one unique date; otherwise it stays ambiguous. No locale
or timezone is inferred, and a parsed reporting date is not proof of trade or
settlement date.

Currency checks count only an explicit native `USD` value; absent or other values
remain unknown and are not defaulted from account balances. Fixed `id`,
`transaction_id` and `reference` checks expose presence, JSON types and within-page
distinct/duplicate scalar counts, never their values. Presence or uniqueness does
not establish identity stability across pages, reports or revisions. Free-text
`trans_str`, descriptions, signs and amounts are not classified as flows or fills.

When exactly one recognized row container exists, pagination checks compare its
row count with bounded native `page`, `per_page` and `total` metadata. A matching
first page whose reported total fits its page size is `reported_single_page`;
larger totals are `more_pages_reported`. Additional `total_pages`, `next_page` or
`pagination` hints are not interpreted; their presence leaves consistency and page
coverage unknown. Inconsistent or missing metadata and ambiguous containers remain
explicit. This only describes the provider's saved
response metadata for the requested window: the original first-page coverage and
`not_qualified` transaction status are preserved. It does not certify complete
history for that window or for other years, nor publish business records.

The existing failed-stage diagnostic also supports `execution_reports_schema_only`
with `funds_schema_only=true` and funds-history inspection disabled. It reads at
most 64 generation-pinned JSON reports from the serving runtime's existing report
root and the shared writer's `firstrade/` platform subdirectory in cloud memory.
Report failures expose only closed reason codes. Only fixed field labels/types,
row counts, dates and explicit account comparisons leave the runner. Unknown fields and absent native
account identity remain unqualified; previewed or submitted orders are not fills.
This mode makes no broker calls and publishes no business records.
