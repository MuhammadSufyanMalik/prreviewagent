# 4. Configuration

There are two configuration layers:

1. **`.env`** — secrets and paths (loaded by `pydantic-settings`).
2. **`config/targets.yaml`** — everything else (loaded and validated by
   `app/config.py`).

Secrets live **only** in `.env`. The YAML references env-var *names*, never
values.

---

## Part A — `.env`

```env
AZURE_DEVOPS_PAT=          # your Azure DevOps PAT (from doc 03)
ANTHROPIC_API_KEY=         # your Anthropic API key
CONFIG_PATH=config/targets.yaml   # which YAML to load
DATABASE_PATH=data/reviews.db     # where the SQLite file lives
LOG_LEVEL=INFO             # DEBUG | INFO | WARNING | ERROR
```

| Key | Meaning |
|-----|---------|
| `AZURE_DEVOPS_PAT` | The token value. Name must match `azure_devops.pat_env`. |
| `ANTHROPIC_API_KEY` | The LLM key. Name must match `llm.api_key_env`. |
| `CONFIG_PATH` | Path to the YAML config. Lets you keep multiple configs. |
| `DATABASE_PATH` | SQLite file path. Created automatically, with parent dirs. |
| `LOG_LEVEL` | Use `DEBUG` while setting up; `INFO` for normal running. |

---

## Part B — `config/targets.yaml`

Below is every section, field by field.

### `azure_devops`

```yaml
azure_devops:
  base_url: "https://dev.azure.company.local/DefaultCollection"
  api_version: "7.1"
  pat_env: "AZURE_DEVOPS_PAT"
  timeout_seconds: 30
  max_retries: 3
```

| Field | Meaning |
|-------|---------|
| `base_url` | Collection URL. On-prem usually ends in the collection name (e.g. `/DefaultCollection`). Trailing slash is stripped automatically. |
| `api_version` | Azure DevOps REST API version. `7.1` is a safe default. |
| `pat_env` | **Name** of the env var holding the PAT. |
| `timeout_seconds` | Per-request HTTP timeout. |
| `max_retries` | How many times to retry transient failures (5xx/429/timeouts) with exponential backoff (2s→4s→8s→16s). |

### `llm`

```yaml
llm:
  provider: "anthropic"
  model: "claude-sonnet-4-6"
  api_key_env: "ANTHROPIC_API_KEY"
  max_diff_chars: 120000
  max_tokens: 4096
  timeout_seconds: 120
```

| Field | Meaning |
|-------|---------|
| `provider` | Currently `anthropic`. (The client is pluggable — see `app/llm_client.py`.) |
| `model` | Anthropic model id. Use a current, capable model. |
| `api_key_env` | **Name** of the env var holding the API key. |
| `max_diff_chars` | Hard cap on how much PR content is sent to the LLM. Larger diffs are truncated to control cost/latency. |
| `max_tokens` | Max tokens in the LLM response. |
| `timeout_seconds` | LLM request timeout. |

### `scheduler`

```yaml
scheduler:
  enabled: true
  interval_seconds: 300
```

| Field | Meaning |
|-------|---------|
| `enabled` | If `false`, no background scanning; you can still trigger scans via the API. |
| `interval_seconds` | How often to scan all enabled targets. 300 = every 5 minutes. |

### `review_policy`

This is the heart of the safety configuration. See
[07-decision-policy.md](07-decision-policy.md) for exactly how each value is used.

```yaml
review_policy:
  dry_run: true
  auto_comment: true
  auto_approve: false
  auto_complete: false

  minimum_confidence_to_approve: 0.90
  minimum_confidence_to_complete: 0.95

  require_successful_build: true
  require_successful_policy: true

  max_changed_files: 50
  max_diff_lines: 3000

  allowed_target_branches:
    - "refs/heads/develop"
    - "refs/heads/main"

  risky_paths:
    - "appsettings.json"
    - ".env"
    - "Dockerfile"
    - "docker-compose"
    - "migrations"
    - "auth"
    - "authorization"
    - "payment"
    - "security"
    - "Program.cs"
    - "Startup.cs"

  merge_strategy: "squash"
  delete_source_branch_after_merge: false
```

| Field | Meaning |
|-------|---------|
| `dry_run` | When `true`, the agent reviews but performs **no** writes. The master safety switch. |
| `auto_comment` | Allow posting review comments. |
| `auto_approve` | Allow casting an approval vote. Independent of merge. |
| `auto_complete` | Allow completing/merging. Independent of approve. |
| `minimum_confidence_to_approve` | LLM confidence (0–1) required to approve. |
| `minimum_confidence_to_complete` | Higher bar required to merge. |
| `require_successful_build` | If `true`, build status must be **successful** (not unknown) to merge. |
| `require_successful_policy` | If `true`, branch policies must be **successful** to merge. |
| `max_changed_files` | PRs with more changed files are not approved/merged. |
| `max_diff_lines` | PRs with more diff lines are not approved/merged. |
| `allowed_target_branches` | Only PRs targeting these branches are acted on. Empty = allow all. |
| `risky_paths` | Case-insensitive substrings. If a changed file matches, the agent becomes conservative and **won't auto-complete**. |
| `merge_strategy` | One of `no_fast_forward`, `squash`, `rebase`, `rebase_merge`. |
| `delete_source_branch_after_merge` | Delete the source branch on merge (only if `true`). |

### `reviewer`

```yaml
reviewer:
  reviewer_id: "abcdef12-3456-...-..."
```

The identity GUID whose vote is cast. See
[03-azure-devops-pat.md](03-azure-devops-pat.md#step-4--find-your-reviewer-guid).

### `targets`

A list of project/repo pairs to scan.

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
    enabled: false        # skipped entirely
```

| Field | Meaning |
|-------|---------|
| `project` | Azure DevOps project name. |
| `repository` | Repository name within the project. |
| `enabled` | If `false`, this target is skipped. |
| `target_branches` | Only active PRs into these branches are fetched for this target. |

> `target_branches` (per target) controls **which PRs are fetched**.
> `review_policy.allowed_target_branches` (global) is an extra gate the
> decision policy enforces before approving/merging. Keep them consistent.

---

## How config is validated

`app/config.py` validates everything with Pydantic on load:

- `merge_strategy` must be one of the four valid values, else startup fails.
- `base_url` trailing slash is stripped.
- `confidence` thresholds are plain floats; keep them in `0.0–1.0`.

If the YAML is malformed or a value is invalid, the app raises a clear error at
startup rather than misbehaving later.

Next: [05-architecture.md](05-architecture.md).
