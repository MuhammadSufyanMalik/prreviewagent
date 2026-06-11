# 1. Overview

## What this is

The PR Review Agent is a small backend service that watches your Azure DevOps
pull requests and acts as an automated first-pass reviewer. It is **local-first**:
you run it on your own machine, it keeps its state in a local SQLite file, and it
talks to Azure DevOps over the REST API.

It does five things, in a strict order:

1. **Reviews** a pull request using an LLM.
2. **Comments** on the PR (a summary thread, plus inline notes on specific lines).
3. **Approves** the PR (casts a reviewer vote) — only if it's safe and you enabled it.
4. **Completes/merges** the PR — only if every required check passes and you enabled it.
5. **Remembers** what it reviewed, so it never reviews the same commit twice.

## The big picture

```
                ┌─────────────────────────────────────────────┐
                │                FastAPI app                   │
                │  /health /status /targets /reviews /scan ... │
                └───────────────┬─────────────────────────────┘
                                │
        APScheduler ──── every N seconds ──── scan_all()
                                │
                                ▼
        ┌───────────────────────────────────────────────┐
        │                  PRService                     │
        │  fetch PR → diff → LLM review → policy → act    │
        └───┬───────────────┬──────────────┬─────────────┘
            │               │              │
            ▼               ▼              ▼
   AzureDevOpsClient   ReviewEngine   decision_policy
   (REST API)          (LLM + JSON)   (deterministic gate)
            │                              │
            ▼                              ▼
     Azure DevOps                       SQLite
```

- **`AzureDevOpsClient`** is the only thing that talks to Azure DevOps.
- **`ReviewEngine`** builds the prompt, calls the LLM, and parses strict JSON.
- **`decision_policy`** is the brain that decides what's *allowed*, independent of
  what the LLM "wants".
- **`PRService`** orchestrates everything and enforces ordering.

## The safety philosophy

Two ideas drive the whole design:

### 1. Never trust the LLM blindly

The LLM returns a `decision` field (e.g. `approve_and_complete`). The agent
**ignores it as an authority**. Instead, a separate, deterministic module
([07-decision-policy.md](07-decision-policy.md)) re-derives what to do from:

- the **severity** of the issues the LLM found,
- the LLM's **confidence** vs. your configured thresholds,
- objective facts: branch, PR size, risky files, build/policy status, conflicts.

If the LLM says "merge it" but a build is failing, the agent does **not** merge.

### 2. Default to doing nothing

Out of the box:

```yaml
dry_run: true          # do the whole review, but make zero changes
auto_approve: false    # never cast a vote
auto_complete: false   # never merge
```

In this state the agent is a safe observer: it tells you what it *would* do and
logs it, but touches nothing in Azure DevOps. You opt in to real actions only
after you trust it — and `auto_approve` and `auto_complete` are deliberately
**separate switches**, because a PR can be safe to approve but not safe to merge.

## What you need

- Python 3.11+
- An Azure DevOps PAT with the right scopes ([03-azure-devops-pat.md](03-azure-devops-pat.md))
- An Anthropic API key
- A few minutes to fill in `config/targets.yaml`

Next: [02-installation.md](02-installation.md).
