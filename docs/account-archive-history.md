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
