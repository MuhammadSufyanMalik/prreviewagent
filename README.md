# PR Review Agent

A lightweight, **local-first** backend service that periodically scans Azure
DevOps pull requests, reviews them with an LLM, posts review comments, and —
only when it is safe and you have explicitly opted in — approves and
completes (merges) them.

Built with **FastAPI + APScheduler + SQLite + YAML**, talking to the **Azure
DevOps REST API** directly (no MCP required for v1).

> 📚 **Detailed, step-by-step documentation** lives in [`docs/`](docs/README.md) —
> one file per topic (installation, PAT, configuration, architecture, the review
> workflow, the decision policy, running, enabling actions, database,
> troubleshooting, Docker, and testing). This README is the quick-start.

---

## 1. What the agent does

On a configurable interval (and on demand) it:

1. Loads config from `.env` + a YAML file.
2. Scans active pull requests in the projects/repos you configure.
3. For each PR: checks whether it's new or has new commits, fetches details,
   changed files, content/diff, existing comments, and build/policy status.
4. Skips PRs that are too large or target a branch you didn't allow.
5. Sends the PR + diff to the LLM, which returns **strict JSON** (summary,
   risk level, issues, positive notes, approval blockers, confidence,
   decision).
6. Posts a summary review comment, plus inline comments on specific file/lines
   where practical.
7. **If enabled and safe**, casts an approval vote using your reviewer identity.
8. **If enabled and all required checks pass**, completes/merges the PR.
9. Records review state in SQLite so the same commit isn't reviewed twice.
10. Logs every action (with secrets redacted).

The agent **never blindly trusts the LLM**. A separate deterministic
`app/decision_policy.py` re-validates every LLM result against your config and
hard safety rules before any vote or merge.

---

## 2. ⚠️ Safety warning

This tool can **approve and merge code**. Treat that power carefully:

- It ships with **`dry_run: true`, `auto_approve: false`, `auto_complete: false`**.
  In this state it makes **no changes** — it only logs what it *would* do.
- Run in dry-run against real PRs until you trust its judgement.
- Enable `auto_approve` first, watch it for a while, and only then consider
  `auto_complete`.
- An LLM can be wrong or manipulated by content inside a PR. The deterministic
  policy is your backstop, but **you are responsible** for what gets merged.
- Keep `require_successful_build` / `require_successful_policy` on for anything
  that auto-merges.

---

## 3. Setup steps

```bash
# 1. Clone and enter the repo
cd prreviewagent

# 2. Create a virtualenv and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Create your env file and config
cp .env.example .env
# edit .env  -> set AZURE_DEVOPS_PAT and ANTHROPIC_API_KEY
# edit config/targets.yaml -> set base_url, reviewer_id, projects/repos
```

`.env`:

```env
AZURE_DEVOPS_PAT=...your PAT...
ANTHROPIC_API_KEY=...your key...
CONFIG_PATH=config/targets.yaml
DATABASE_PATH=data/reviews.db
LOG_LEVEL=INFO
```

---

## 4. How to create an Azure DevOps PAT

1. Sign in to Azure DevOps (Server or Services).
2. Click your **user settings** (top-right avatar) → **Personal access tokens**.
3. **New Token**. Give it a name and an expiry.
4. Select the scopes (see below).
5. **Create**, then copy the token immediately — you won't see it again.
6. Put it in `.env` as `AZURE_DEVOPS_PAT`.

The PAT identity is the user whose review vote is cast. Make sure that user is
allowed to be a reviewer on the target repos, and set `reviewer.reviewer_id` to
that identity's GUID.

> **Finding the reviewer GUID:** call
> `GET {base_url}/_apis/connectionData` while authenticated with the PAT — the
> response includes `authenticatedUser.id`. Use that GUID as `reviewer_id`.

---

## 5. Required PAT permissions

| Capability                         | Scope                                   |
| ---------------------------------- | --------------------------------------- |
| Read PRs, iterations, files, diffs | **Code (Read)**                         |
| Post comments, cast votes, complete| **Code (Read & Write)**                 |
| Read build/policy status           | **Code (Read)** (+ **Build (Read)** if you use build policies) |

In short: **Code = Read & Write** is the minimum for full functionality.
For dry-run / read-only evaluation, **Code = Read** is enough.

---

## 6. How to configure projects/repositories

Edit `config/targets.yaml`. Secrets are **not** stored here — only the *names*
of the env vars that hold them (`pat_env`, `api_key_env`).

```yaml
targets:
  - project: "ProjectA"
    repository: "RepoA"
    enabled: true
    target_branches:
      - "refs/heads/develop"
      - "refs/heads/main"
  - project: "ProjectB"
    repository: "RepoB"
    enabled: false        # disabled targets are skipped
```

Key policy knobs (`review_policy:`):

- `allowed_target_branches` — only PRs into these branches are acted on.
- `max_changed_files` / `max_diff_lines` — larger PRs are skipped for approval/merge.
- `risky_paths` — substrings (auth, payment, migrations, Dockerfile, …) that
  make the agent more conservative (it won't auto-complete a PR touching them).
- `minimum_confidence_to_approve` / `minimum_confidence_to_complete`.
- `require_successful_build` / `require_successful_policy`.
- `merge_strategy` — `no_fast_forward` | `squash` | `rebase` | `rebase_merge`.

---

## 7. How to run locally

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

The background scheduler starts automatically (if `scheduler.enabled: true`).

Endpoints:

| Method | Path                                              | Purpose                              |
| ------ | ------------------------------------------------- | ------------------------------------ |
| GET    | `/health`                                         | Liveness check                       |
| GET    | `/status`                                          | Scheduler + policy flags             |
| GET    | `/targets`                                          | Configured targets                   |
| GET    | `/reviews?limit=50`                                 | Recent review records                |
| POST   | `/scan`                                             | Trigger a full scan now              |
| POST   | `/review/{project}/{repository}/{pull_request_id}`  | Review one PR (honours config flags) |
| POST   | `/dry-run/{project}/{repository}/{pull_request_id}` | Review one PR, forced dry-run        |

Example:

```bash
curl -X POST http://localhost:8000/dry-run/ProjectA/RepoA/123
```

---

## 8. How dry-run works

When `review_policy.dry_run: true` (the default), the agent does the **entire**
review — fetches the PR, runs the LLM, evaluates the deterministic policy — and
then **stops before any write**. No comments, votes, or merges happen. The
result (and what it *would* have done) is logged and stored, and returned by the
API as `status: "dry_run"` with a human-readable reason string.

The `POST /dry-run/...` endpoint forces dry-run for a single PR even if config
has it disabled — handy for spot-checking.

---

## 9. How to enable auto approve

Once you trust dry-run output:

```yaml
review_policy:
  dry_run: false        # the agent may now act
  auto_comment: true
  auto_approve: true    # <— enable approvals
  auto_complete: false  # still no merging
```

The agent will **always comment before approving**, and will only approve when:
the deterministic vote is an approval, confidence ≥ `minimum_confidence_to_approve`,
the branch is allowed, and the PR is within size limits.

Vote mapping (Azure DevOps): `10` approved · `5` approved with suggestions ·
`0` no vote · `-5` waiting for author · `-10` rejected.

---

## 10. How to enable auto complete / merge

`auto_approve` and `auto_complete` are **deliberately separate** — a PR can be
safe to approve but not safe to merge (build unknown, policies still running,
conflicts). Enable completion only after approvals look right:

```yaml
review_policy:
  dry_run: false
  auto_approve: true
  auto_complete: true   # <— enable merging
  require_successful_build: true
  require_successful_policy: true
  merge_strategy: "squash"
  delete_source_branch_after_merge: false
```

The agent will complete a PR **only if all** of these hold:

- review comment posted successfully, **and** approval vote posted successfully
- deterministic vote is a full approve (`10`)
- no critical / high / medium issues, and no approval blockers
- confidence ≥ `minimum_confidence_to_complete`
- target branch allowed, PR size within limits, no risky paths touched
- no merge conflicts
- build successful (if required), policy successful (if required)
- not in dry-run mode

If any required signal **cannot be verified** (e.g. build status unknown), the
agent does **not** merge.

---

## 11. How to run a manual review

```bash
# Honour configured flags (may comment/approve/complete if enabled):
curl -X POST http://localhost:8000/review/ProjectA/RepoA/123

# Safe preview, never writes:
curl -X POST http://localhost:8000/dry-run/ProjectA/RepoA/123

# Scan everything now:
curl -X POST http://localhost:8000/scan
```

Manual reviews use `force=True`, so they re-review even a commit that was
already reviewed.

---

## 12. How to later Dockerize

v1 is intentionally local. To containerize later, add a `Dockerfile` like:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY config ./config
ENV CONFIG_PATH=config/targets.yaml DATABASE_PATH=/data/reviews.db
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Run with secrets injected as env vars and the DB on a mounted volume:

```bash
docker build -t pr-review-agent .
docker run -p 8000:8000 \
  -e AZURE_DEVOPS_PAT=... -e ANTHROPIC_API_KEY=... \
  -v $(pwd)/data:/data \
  pr-review-agent
```

---

## Project layout

```
app/
  main.py              # FastAPI app + wiring + lifespan
  config.py            # .env + YAML config models
  scheduler.py         # APScheduler background job
  db.py / models.py    # SQLite storage
  azure_devops_client.py  # REST client (retries, timeouts, vote/complete)
  llm_client.py        # Anthropic wrapper
  review_engine.py     # prompt + strict-JSON parsing
  decision_policy.py   # deterministic safety gate (the backstop)
  pr_service.py        # orchestration: comment -> approve -> complete
  dashboard.py         # FastAPI routes
  logging_config.py    # structured logging + secret redaction
config/targets.yaml
tests/                 # decision policy, config, orchestration tests
```

## Running tests

```bash
source .venv/bin/activate
pytest -q
```

---

## Optional future: MCP

After v1 is solid, the Azure DevOps operations can be exposed as an MCP server
(`list_pull_requests`, `get_pull_request`, `get_pull_request_diff`,
`post_pull_request_comment`, `set_reviewer_vote`, `complete_pull_request`) so
Claude Code or other agents can call them as tools. Not implemented in v1.
