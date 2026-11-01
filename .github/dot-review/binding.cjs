// Pure classification and binding helpers adapted from QuantAIService PR #450.
// This module neither evaluates approval nor publishes a commit status.
'use strict';

const { createHash } = require('node:crypto');

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}
const digest = value => createHash('sha256').update(canonical(value)).digest('hex');
const sha = value => typeof value === 'string' && /^[a-f0-9]{40}$/.test(value);
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const matches = (path, rule) => rule.endsWith('/') ? path.startsWith(rule) : path === rule;

function pathTier(path, policy) {
  assert(typeof path === 'string' && path.length > 0 && !path.startsWith('/')
    && !path.includes('\\') && !path.split('/').some(p => p === '..' || p === '.' || p === ''), 'Unknown path');
  const p = path.replace(/([a-z0-9])([A-Z])/g, '$1_$2').toLowerCase();
  // Protected paths take precedence over prose, tests, examples and fixtures.
  if (p.startsWith('.github/') || /(^|\/)(agents|claude)\.md$/.test(p)
    || /(^|\/)(codeowners|\.coderabbit\.ya?ml|\.env(?:[._-].*)?)$/.test(p)
    || /(^|[\/_.-])(auth(?:entication|orization)?|oauth|login|session|tokens?|credentials?|secrets?|iam|permissions?|privacy|storage|trad(?:e|es|ing)|execut(?:ion|or)s?|orders?|brokers?|risk|deploy(?:ment)?|runtime|strateg(?:y|ies)|governance)([\/_.-]|$)/.test(p)
    || /(^|\/)(dockerfile(?:\..*)?|docker-compose[^/]*|compose\.ya?ml|vercel\.json|wrangler\.(?:toml|jsonc?)|firebase\.json|package(?:-lock)?\.json|requirements[^/]*|pyproject\.toml|setup\.(?:py|cfg)|uv\.lock|poetry\.lock|yarn\.lock|pnpm-lock\.yaml|makefile|.*\.config\.[^/]+)$/.test(p)
    || /\.(?:tf|tfvars|sh|bash|ps1)$/.test(p)
    || policy.r2_paths.some(rule => matches(path, rule))) return 'R2';
  return policy.r0_paths.some(rule => matches(path, rule)) && /\.(?:md|txt)$/.test(p) ? 'R0' : 'R1';
}

function classify(files, expected, policy) {
  assert(Array.isArray(files) && files.length > 0 && files.length === expected && files.length < 3000, 'Incomplete file list');
  assert(new Set(files.map(f => f.filename)).size === files.length, 'Duplicate file');
  let tier = 'R0';
  const rules = [];
  for (const file of files) {
    assert(['added', 'modified', 'removed', 'renamed', 'copied', 'changed'].includes(file.status), 'Unknown file status');
    assert(sha(file.sha), 'Missing file object SHA');
    assert(Number.isSafeInteger(file.additions) && file.additions >= 0 && Number.isSafeInteger(file.deletions) && file.deletions >= 0, 'Unknown diff size');
    if (file.status === 'renamed') assert(file.previous_filename, 'Missing previous path');
    const paths = [file.filename, file.previous_filename].filter(Boolean);
    let fileTier = paths.map(p => pathTier(p, policy)).sort().at(-1);
    // Count actual hunk lines: missing/truncated/binary patches cannot attest coverage.
    const patch = file.patch || '';
    const lines = patch.split('\n');
    assert(lines.filter(l => l.startsWith('+')).length === file.additions
      && lines.filter(l => l.startsWith('-')).length === file.deletions, 'Incomplete patch');
    assert(patch || (file.additions === 0 && file.deletions === 0 && file.status === 'renamed'), 'Missing patch');
    const changed = lines.filter(l => /^[+-]/.test(l)).map(l => l.slice(1));
    // R0 is deliberately narrow: even inline code, links and HTML need a reviewer.
    if (fileTier === 'R0' && (file.status !== 'modified' || changed.some(l => /[`~<>\[\]{}\\$]|^\s{4}|(?:https?:|javascript:|\b(?:curl|wget|npm|python|sudo|bash)\b)/i.test(l)))) fileTier = 'R1';
    if (paths.some(p => /\.(?:md|txt)$/.test(p)) && changed.some(l => /\b(?:auth|iam|secret|credential|permission|trading|orders?|strategy|deploy|workflow|owner-approved)\b|认证|授权|凭据|交易|下单|部署/i.test(l))) fileTier = 'R2';
    if (fileTier > tier) tier = fileTier;
    rules.push({ paths, tier: fileTier });
  }
  return { tier, rules };
}

function receipt(body, schema) {
  // A whole review/comment body must be a JSON object, optionally in one JSON fence.
  try {
    const text = (body || '').trim().replace(/^```json\s*\n([\s\S]*?)\n```$/, '$1');
    const value = JSON.parse(text);
    return value?.schema_version === schema ? value : null;
  } catch { return null; }
}

function binding(snapshot, policySha) {
  const pr = snapshot.pr;
  assert(sha(pr.head.sha) && sha(pr.base.sha) && sha(snapshot.policyCommit), 'Unknown commit');
  return {
    repository: snapshot.repository, repository_id: pr.base.repo.id, pr_number: pr.number,
    head_sha: pr.head.sha, base_sha: pr.base.sha, policy_sha256: policySha,
    diff_sha256: digest(snapshot.files.map(f => ({ filename: f.filename, previous_filename: f.previous_filename || null,
      status: f.status, sha: f.sha, additions: f.additions, deletions: f.deletions, patch: f.patch || '' }))
      .sort((a, b) => a.filename < b.filename ? -1 : a.filename > b.filename ? 1 : 0)),
  };
}

function stableComment(comment) {
  // Reactions and profile decoration are not approval or review evidence.
  const { id, body, created_at, updated_at, path, line, original_line, side,
    start_line, start_side, commit_id, original_commit_id, in_reply_to_id,
    pull_request_review_id } = comment;
  return { id, body, created_at, updated_at, path, line, original_line, side,
    start_line, start_side, commit_id, original_commit_id, in_reply_to_id,
    pull_request_review_id,
    user: { id: comment.user?.id, login: comment.user?.login, type: comment.user?.type } };
}

function stableReview(review) {
  return { ...stableComment(review), state: review.state, submitted_at: review.submitted_at };
}

function discussion(snapshot) {
  // Review receipts are excluded to avoid a circular digest. Their exact bodies
  // are separately bound into the owner approval. QAS shadow output is advisory.
  const reviews = snapshot.reviews.filter(r => r.state === 'CHANGES_REQUESTED'
    || (!receipt(r.body, 'pr-review/v1') && !receipt(r.body, 'owner-approval/v1'))).map(stableReview);
  const secondOpinions = snapshot.issueComments.filter(c => c.user?.type === 'Bot' && c.user?.login === 'coderabbitai[bot]').map(stableComment);
  return { reviews, comments: snapshot.reviewComments.map(stableComment), threads: snapshot.threads, secondOpinions };
}


module.exports = { canonical, digest, classify, pathTier, binding, discussion, stableComment, stableReview };

async function readThreads(github, repo, number) {
  const threads = [];
  let cursor = null;
  do {
    const data = await github.graphql(`query($owner:String!,$repo:String!,$number:Int!,$cursor:String) {
      repository(owner:$owner,name:$repo) { pullRequest(number:$number) {
        reviewThreads(first:100,after:$cursor) {
          nodes { id isResolved isOutdated }
          pageInfo { hasNextPage endCursor }
        }
      } }
    }`, { ...repo, number, cursor });
    const page = data.repository?.pullRequest?.reviewThreads;
    if (!page || !Array.isArray(page.nodes) || !page.pageInfo) throw new Error('Incomplete threads');
    threads.push(...page.nodes);
    const next = page.pageInfo.hasNextPage ? page.pageInfo.endCursor : null;
    if (page.nodes.some(t => !t.id || typeof t.isResolved !== 'boolean') || (page.pageInfo.hasNextPage && (!next || next === cursor))) throw new Error('Invalid thread page');
    cursor = next;
  } while (cursor);
  return threads;
}


module.exports.readThreads = readThreads;
