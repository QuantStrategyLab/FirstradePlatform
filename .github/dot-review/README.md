# Dot review submission and collection

Follow [AGENTS.md](../../AGENTS.md). These workflows request read-only advice;
they grant no code changes, deployment, trading or approval authority.

`dot-review-submit.yml` runs trusted default-branch code after the exact
`PR risk gate` workflow completes, including a failed gate. It independently
reads GitHub and classifies changed paths. Only open, non-draft R2 PRs are
submitted automatically. Missing associations require manual dispatch.
No PR checkout, cache, script, upstream output or artifact is executed.

The classification, diff and discussion helpers are adapted from QuantAIService
PR #450. `policy.json` defines this repository's paths. The policy digest binds
the local helper, classifier, policy and all three related workflows. This
does not install #450's evaluator or change the existing risk gate. In particular,
these drafts cannot satisfy the old gate or be relabeled as accepted #450 evidence.
A future publisher must compute the then-current gate binding independently.

## Enablement

All switches default off. Before enabling `DOT_REVIEW_READY`, an authorized
operator must read back the deployed service version, exact audience, six
per-workflow/event grants, project mappings/policies, Dot client project access,
OAuth scopes and each project's active `task.requested` subscription. This
variable records operator readiness; it is not itself subscription evidence.
If that readback is unavailable or a connection/subscription change is needed,
keep submission disabled. Recheck immediately before first dispatch. The service
also refuses work without an admitted project and fails undispatched Dot tasks
when no authorized active subscription exists.

Configure existing repository variables `AI_SERVICE_URL` (HTTPS origin),
`AI_SERVICE_AUDIENCE` (the exact approved audience), `DOT_REVIEW_PROFILE` and
`DOT_REVIEW_MODEL` from protected configuration. No service token, GitHub App,
PAT, environment subject or new secret is used. Requests must fit both local
limits in `policy.json` and the service's project policy. Never increase the
service policy or select another model merely to make a request pass.

Manual submission on `main` takes `pr_number`, a full `target_sha`,
`review_kind` and optional `expected_base_sha`. Premerge binds current head/base;
posthoc requires the actual merged commit and its sole parent. The original PR
head is retained separately. Perform the approved first posthoc review completely,
then the second, before separately enabling `DOT_REVIEW_AUTO_ENABLED`.
No scheduled collector is installed.

## Recovery and results

The complete frozen request is uploaded before POST. A bot intent comment stores
its SHA-256, stable idempotency key and artifact locator before network submission.
An existing intent, a lost POST response or any ambiguous outcome leads only to
lookup with the original request hash. Preparation first finds the existing intent
for the same review kind and target SHA, then reads the original task to restore
its frozen request and key. Changed CI records or model settings cannot authorize
a new POST. An unavailable lookup, mismatched request or changed binding stops
recovery; it never creates a replacement intent. A lookup miss remains unknown. Do not
delete the comment, change the key, change the request, or dispatch again as a
way to recover an unknown task. Use the collector with the original locator
comment ID; it supports lookup even when the POST returned no task ID.

Collect manually on `main` with `locator_comment_id` and optionally the expected
`task_id`. Collection never submits, appends, cancels, claims or reports a task.
It validates the project, frozen request, binding, provider, attempt, revision,
execution provenance and review completeness. It then rechecks the GitHub snapshot.
Changed head, base, policy or discussion produces stale evidence. Nonterminal and
failed results remain such. Only one result comment per task is updated.

`posthoc-ai-review/v1` is an archive contract for closed PRs, not premerge approval.
Both premerge and posthoc output is `bot-draft-only`, with `approval: false`.
`technical_result_valid` describes the checked technical output, not gate approval.
The reported model/run reference comes from the authenticated worker result;
it is not independent provider attestation. A publisher must inspect that real
execution before issuing any separate User evidence. No owner approval record,
override, label, native approval review or commit status is created here.

Incomplete pages, capped API results, missing or binary source, symlinks,
truncated patches and oversized requests fail closed without silent truncation.
Source includes changed files at base and target, applicable AGENTS files and
engineering instructions. Additional dependencies remain a reviewer's explicit
coverage responsibility; missing context must be reported as a limitation.

Run the offline checks with `node --test .github/dot-review/test-*.cjs` and
`actionlint .github/workflows/dot-review-{submit,collect}.yml`.
