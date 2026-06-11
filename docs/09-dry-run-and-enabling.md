# 9. Dry-run, then safely enabling actions

This is the most important operational doc. Follow it in order. Do **not** skip
straight to enabling merges.

## The four switches

```yaml
review_policy:
  dry_run: true          # master safety: true = no writes at all
  auto_comment: true     # allow posting comments
  auto_approve: false    # allow casting votes
  auto_complete: false   # allow merging
```

They form a ladder. Climb one rung at a time, observing for a while at each.

```
Stage 0  dry_run: true                          → observe only
Stage 1  dry_run: false, auto_comment: true      → comments only
Stage 2  + auto_approve: true                     → comments + votes
Stage 3  + auto_complete: true                     → comments + votes + merges
```

---

## Stage 0 — Dry-run (start here, stay a while)

With `dry_run: true`, the agent does the **entire** pipeline — fetch, LLM review,
deterministic policy — then stops before any Azure DevOps write. Nothing is
commented, voted, or merged.

How to use it:

```bash
# Force a dry-run on a specific PR regardless of config:
curl -s -X POST http://localhost:8000/dry-run/ProjectA/RepoA/123 | python -m json.tool
```

Read the `message` field — it tells you exactly what *would* have happened and
**why**:

```
[DRY-RUN] would comment=True approve=False (vote=0) complete=False;
reasons: commenting enabled; vote 0 is not an approval (issues/blockers present);
risky paths touched: src/auth/Login.cs
```

Also watch the logs and `GET /reviews`. Spend real time here:
- Do the LLM's issues look sensible?
- Are the votes (10/5/0/-5/-10) what you'd expect?
- Are size/branch/risky-path gates behaving?

Only move on when the dry-run decisions consistently match your judgement.

---

## Stage 1 — Comments only

```yaml
review_policy:
  dry_run: false
  auto_comment: true
  auto_approve: false
  auto_complete: false
```

Now the agent posts review comments (summary + inline) but never votes or
merges. This is low-risk and immediately useful: your PRs get automated review
notes. Confirm the comment formatting and inline anchoring look good on real PRs.

> Requires the PAT to have **Code (Read & Write)** so it can post threads.

---

## Stage 2 — Auto-approve

```yaml
review_policy:
  dry_run: false
  auto_comment: true
  auto_approve: true
  auto_complete: false
  minimum_confidence_to_approve: 0.90
```

The agent will now cast votes — but **only** after a successful comment, and only
when the deterministic policy says it's an approval with enough confidence on an
allowed branch within size limits. It will **never merge** at this stage.

What to watch:
- Are approvals landing on the PRs you'd approve yourself?
- Tune `minimum_confidence_to_approve` up if it's too eager.
- Remember: the vote may be `5` (approve with suggestions) when only low/info
  issues exist — that's intentional and does not lead to a merge.

---

## Stage 3 — Auto-complete (merge)

Only after Stage 2 has been reliable for a while.

```yaml
review_policy:
  dry_run: false
  auto_comment: true
  auto_approve: true
  auto_complete: true
  minimum_confidence_to_complete: 0.95
  require_successful_build: true
  require_successful_policy: true
  merge_strategy: "squash"
  delete_source_branch_after_merge: false
```

The agent will complete a PR **only if every** condition in
[07-decision-policy.md](07-decision-policy.md#how-completion-merge-is-decided)
holds, including: comment posted, vote was a full approve, no medium/high/critical
issues, no blockers, confidence ≥ complete threshold, branch allowed, size OK, no
risky paths, no conflicts, and build/policy successful (if required). If any
required signal is **unknown**, it does not merge.

### Why `auto_approve` and `auto_complete` are separate

A PR can be perfectly fine to **approve** yet unsafe to **merge** right now:
- the build is still running (status unknown),
- branch policies haven't finished,
- there's a merge conflict.

Keeping the switches separate lets the agent give its approval signal while
leaving the actual merge for when (and if) the objective gates are green.

---

## Recommended rollout checklist

- [ ] Stage 0 dry-run looks correct across many real PRs
- [ ] PAT upgraded to **Code (Read & Write)**
- [ ] Stage 1 comments look good (formatting + inline)
- [ ] Stage 2 approvals match your judgement; thresholds tuned
- [ ] `require_successful_build` / `require_successful_policy` enabled
- [ ] `risky_paths` covers your sensitive areas (auth, payments, migrations, infra)
- [ ] `allowed_target_branches` limited to the branches you trust to auto-merge
- [ ] Only then: Stage 3 auto-complete

## Emergency stop

To instantly make the agent passive again without stopping the process, set
`dry_run: true` (or `auto_approve: false` / `auto_complete: false`) and restart
the server. For a hard stop, `Ctrl+C`.

Next: [10-database.md](10-database.md).
