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
counts leave the runner. Fixed labels include snake_case, camelCase and compact
spellings of date, activity/type, currency and record metadata; arbitrary keys
and all values remain private. A recognized label does not establish its business
meaning. Missing or invalid counts remain null; an explicit
provider error stops inspection without exporting its text. Pagination counts
describe the returned first-page metadata and do not certify source coverage. A first
page is not complete history, a missing container is not an empty result, and
field presence alone does not qualify a transaction as an external flow or fill.
This inspection does not publish account facts, returns, flows or executions,
deploy an image, change permissions, notify, or create a schedule.

The existing failed-stage diagnostic also supports `execution_reports_schema_only`
with `funds_schema_only=true` and funds-history inspection disabled. It reads at
most 64 generation-pinned JSON reports from the serving runtime's existing report
root and the shared writer's `firstrade/` platform subdirectory in cloud memory.
Report failures expose only closed reason codes. Only fixed field labels/types,
row counts, dates and explicit account comparisons leave the runner. Unknown fields and absent native
account identity remain unqualified; previewed or submitted orders are not fills.
This mode makes no broker calls and publishes no business records.
