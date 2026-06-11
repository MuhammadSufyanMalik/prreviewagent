# 6. The review workflow, step by step

This is exactly what happens when the agent reviews one pull request. The code
lives in `app/pr_service.py` (`review_pull_request`) and
`app/decision_policy.py` (`evaluate`).

## Step 0 — Trigger

A review starts in one of three ways:

- **Scheduled**: the APScheduler job fires every `interval_seconds` and calls
  `scan_all()`, which scans every enabled target.
- **Manual scan**: `POST /scan`.
- **Single PR**: `POST /review/{project}/{repo}/{prId}` (honours config) or
  `POST /dry-run/...` (forces dry-run).

Scheduled/scan reviews skip already-reviewed commits; manual single-PR reviews
use `force=True` and re-review.

## Step 1 — Fetch PR details

`get_pull_request(...)` returns title, description, source/target branch,
`mergeStatus`, and `lastMergeSourceCommit`. From `mergeStatus == "conflicts"` we
derive `has_conflicts`.

If this call fails, the outcome is `error` and nothing else happens.

## Step 2 — Determine the latest commit & skip check

`get_latest_commit_id(...)` reads the PR's iterations and takes the source commit
of the most recent one. This commit id is the **dedupe key**.

Unless `force=True`, the agent looks up
`reviewed_pull_requests(project, repo, pr_id, latest_commit_id)`. If a row
exists, the PR at this commit was already reviewed → **status `skipped`**, done.
This is what stops the agent re-reviewing the same code every interval. When the
author pushes a new commit, the commit id changes and the PR is reviewed again.

## Step 3 — Gather the change set

`get_changed_files(...)` lists the changed paths from the latest iteration. These
feed two things:
- the **risky paths** check (do any match `risky_paths`?),
- the **size** check (`changed_files_count`).

## Step 4 — Build reviewable content (the "diff")

`_collect_diff_text(...)` assembles the text sent to the LLM. Azure DevOps
doesn't expose a single clean unified-patch endpoint, so the agent pulls the
current content of each changed file at the source commit and concatenates it,
capped at `llm.max_diff_chars`. The line count of this text becomes `diff_lines`
for the size gate.

> If a file's content can't be fetched, it's marked `(content unavailable)`
> rather than failing the whole review.

## Step 5 — Fetch existing comments

`get_threads(...)` returns existing PR threads; their comment text is summarized
and passed to the LLM as context so it doesn't repeat points already raised. If
threads can't be fetched, the agent logs a warning and continues with none.

## Step 6 — Fetch build/policy status

`_collect_status(...)` calls `get_policy_evaluations(...)` and classifies each
evaluation into **build** vs **other policy**, returning two tri-state values:

- `True` — all relevant evaluations are `approved`,
- `False` — at least one is not approved,
- `None` — unknown/unavailable.

`None` matters: when `require_successful_build`/`require_successful_policy` is
on, unknown counts as **not satisfied**, so the agent won't merge.

## Step 7 — Assemble `PRContext`

All the objective facts are packed into a `PRContext`: target branch, file count,
diff lines, changed paths, build/policy tri-states, and conflict flag. This is
the LLM-independent view of the PR that the policy will judge.

## Step 8 — Run the LLM review

`ReviewEngine.build_user_prompt(...)` + `review(...)`:
- builds the prompt (PR metadata + changed files + capped content + existing
  comments),
- calls the LLM with the strict-JSON system prompt,
- parses and validates the response into a `ReviewResult`.

If the LLM errors or returns invalid JSON → **status `error`**, the review row is
persisted with the error, and **no approve/merge** happens.

## Step 9 — Deterministic policy evaluation

`decision_policy.evaluate(review, ctx, policy)` returns a `PolicyDecision` with
`should_comment`, `should_approve`, `should_complete`, the `vote`, and reasons.
This is where the LLM's `decision` field is **ignored** in favour of objective
rules. Full detail in [07-decision-policy.md](07-decision-policy.md).

## Step 10 — Dry-run short-circuit

If dry-run is in effect (config `dry_run: true`, or the `/dry-run` endpoint, or
`dry_run=True` passed in):
- the agent logs a human-readable summary of what it *would* do,
- records the review (and a `dry_run` audit action),
- returns **status `dry_run`**,
- makes **no** Azure DevOps writes.

## Step 11 — Execute actions (comment → approve → complete)

When not in dry-run, `_execute_actions(...)` runs the ordered pipeline. Each step
gates the next:

### 11a. Comment (always first)
If `should_comment`, post the summary thread, then inline comments for issues
that carry a file+line. Inline anchoring failures are tolerated (the issue still
appears in the summary). If the **summary** comment fails → status `error`,
**stop** (no approve/merge). On success, `outcome.commented = True`.

### 11b. Approve (only after a successful comment)
If `should_approve`:
- guard: refuse if `commented` is not `True`,
- call `set_reviewer_vote(...)` with the deterministic `vote` (10/5),
- on failure → status `error`, **stop** (no merge),
- on success, `outcome.approved = True`.

### 11c. Complete (only after a successful approval)
If `should_complete`:
- guard: refuse unless both `commented` and `approved` are `True`,
- re-fetch the merge source commit,
- call `complete_pull_request(...)`, which **re-checks** safety: not dry-run,
  preconditions met, no conflicts, build/policy successful if required,
- on success, `outcome.completed = True`.

## Step 12 — Persist

`_persist(...)` upserts the `reviewed_pull_requests` row (decision, confidence,
risk, comment_posted, approved, completed, error). Each action also wrote a row
to `review_actions` for the audit trail.

## Step 13 — Logging

Every meaningful step logs at `INFO`: the LLM decision, the computed vote, the
three booleans, and each action's success/failure. Secrets are redacted.

## The ordering guarantees (summary)

| Guarantee | Enforced by |
|-----------|-------------|
| Always comment before approving | `_execute_actions` order + `commented` guard |
| Approve only after a successful review | `error` short-circuit on review failure |
| Don't approve if comment failed | early `return` after comment failure |
| Don't merge if approval failed | early `return` after vote failure |
| Don't merge if build/policy unknown & required | tri-state `None` → fail in policy + re-check in `complete_pull_request` |
| Don't merge in dry-run | dry-run short-circuit + guard inside `complete_pull_request` |

Next: [07-decision-policy.md](07-decision-policy.md).
