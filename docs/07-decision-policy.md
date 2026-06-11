# 7. The decision policy (the safety gate)

`app/decision_policy.py` is the most important module for safety. It is **pure**
(no network, no LLM calls) and **deterministic**: same inputs → same decision.
The LLM's `decision` field is treated as a *suggestion only* and never directly
drives a vote or merge.

## Inputs

```python
evaluate(review: ReviewResult, ctx: PRContext, policy: ReviewPolicy) -> PolicyDecision
```

- **`review`** — the parsed LLM output: `issues` (each with a `severity`),
  `confidence`, `approval_blockers`, etc.
- **`ctx: PRContext`** — objective facts: target branch, changed files count,
  diff lines, changed paths, `build_succeeded`/`policy_succeeded`/`has_conflicts`
  (tri-state: `True`/`False`/`None`).
- **`policy: ReviewPolicy`** — your config thresholds and switches.

## Output

```python
@dataclass
class PolicyDecision:
    should_comment: bool
    should_approve: bool
    should_complete: bool
    vote: int                 # Azure DevOps vote value
    reasons: list[str]        # human-readable explanations
    blockers: list[str]       # hard blockers that stop approve/merge
```

## Azure DevOps vote values

| Constant | Value | Meaning |
|----------|-------|---------|
| `VOTE_APPROVE` | `10` | Approved |
| `VOTE_APPROVE_WITH_SUGGESTIONS` | `5` | Approved with suggestions |
| `VOTE_NO_VOTE` | `0` | No vote |
| `VOTE_WAIT_FOR_AUTHOR` | `-5` | Waiting for author |
| `VOTE_REJECT` | `-10` | Rejected |

## How the vote is computed (`compute_vote`)

Severity and blockers drive the vote, **in this order**:

```
critical issue present        → -10  (reject)
high issue present            →  -5  (wait for author)
any approval_blockers present →  -5  (wait for author)
medium issue present          →   0  (no vote — surfaced, not approved)
only low/info issues          →   5  (approve with suggestions)
no issues at all              →  10  (approve)
```

This maps directly to the requested decision policy:

- Any **critical** → reject.
- Any **high** → waiting for author.
- **Medium** issues → not approved (and never auto-completed).
- Only **low/info** → approve with suggestions.
- **None** → full approve.

## How approval is decided

`should_approve` is `True` only if **all** hold:

1. `policy.auto_approve` is `true`,
2. the computed vote is an approval (`10` or `5`),
3. `review.confidence >= policy.minimum_confidence_to_approve`,
4. the target branch is allowed,
5. the PR size is within limits,
6. there are no hard blockers.

If any fail, `should_approve` is `False` and a reason is recorded.

## How completion (merge) is decided

`should_complete` is the strictest gate. It requires `policy.auto_complete` and
**every** one of these checks to pass:

| Check | Why |
|-------|-----|
| `auto_complete` enabled | explicit opt-in |
| agent will approve first | merge follows approval |
| vote is full approve (`10`) | "approve with suggestions" is not enough to merge |
| no critical issues | obvious |
| no high issues | correctness blockers |
| no medium issues | conservative: medium → don't auto-merge |
| no approval blockers | author must resolve them |
| confidence ≥ `minimum_confidence_to_complete` | higher bar than approve |
| branch allowed | only merge into allowed targets |
| size within limits | don't auto-merge huge PRs |
| no merge conflicts (`has_conflicts != True`) | can't safely merge |
| no risky paths touched | auth/payment/migrations/etc. → human required |
| build succeeded (if required) | `None`/unknown fails this |
| policy succeeded (if required) | `None`/unknown fails this |

The **tri-state** matters: when `require_successful_build` is on and
`build_succeeded` is `None` (unknown), the check fails and the agent does **not**
merge. "Unknown" is treated as "not safe".

## Hard blockers vs. reasons

- **`blockers`** are conditions that disqualify approve/merge entirely
  (disallowed branch, oversize PR). They are checked first and feed both
  `should_approve` and `should_complete`.
- **`reasons`** are explanatory strings (e.g. "risky paths touched: src/auth/…",
  "confidence 0.82 below approve threshold 0.90"). They show up in dry-run output
  and logs so you understand *why* the agent did what it did.

## Worked examples

| Scenario | vote | approve | complete |
|----------|------|---------|----------|
| Clean PR, confidence 0.99, build+policy OK | 10 | ✅ | ✅ |
| One **low** style nit | 5 | ✅ | ❌ (not a full approve) |
| One **medium** issue | 0 | ❌ | ❌ |
| One **high** bug | -5 | ❌ | ❌ |
| One **critical** vuln + blocker | -10 | ❌ | ❌ |
| Clean, but confidence 0.92 (≥0.90, <0.95) | 10 | ✅ | ❌ (below complete bar) |
| Clean, but build status unknown | 10 | ✅ | ❌ (build required) |
| Clean, but targets `feature/x` (not allowed) | 10 | ❌ | ❌ |
| Clean, but touches `src/auth/Login.cs` | 10 | ✅ | ❌ (risky path) |
| Clean, but `auto_approve: false` | 10 | ❌ | ❌ |

All of these are covered by unit tests in
`tests/test_decision_policy.py` — see [13-testing.md](13-testing.md).

## Defense in depth

Even after the policy says `should_complete = True`, `PRService.complete_pull_request`
**re-verifies** the dangerous conditions (dry-run off, preconditions met, no
conflicts, build/policy successful) immediately before calling the merge API. A
single bug in one layer doesn't lead to an unsafe merge.

Next: [08-running.md](08-running.md).
