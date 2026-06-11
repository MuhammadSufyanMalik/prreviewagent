# 11. Troubleshooting

## Startup

### `FileNotFoundError: Config file not found: config/targets.yaml`
`CONFIG_PATH` points at a missing file. Check `.env` and that the path is
relative to where you launch uvicorn (or use an absolute path).

### `merge_strategy must be one of [...]`
`review_policy.merge_strategy` must be exactly one of `no_fast_forward`,
`squash`, `rebase`, `rebase_merge`.

### `ValidationError` on startup
The YAML has a missing/invalid field. The error names the field. Common cases:
missing `reviewer.reviewer_id`, missing `azure_devops.base_url`.

### Warnings: "Azure DevOps PAT is empty" / "LLM API key is empty"
The env vars named by `pat_env` / `api_key_env` are not set. Fill them in `.env`.
The app still starts so you can hit read-only endpoints, but reviews/API calls
will fail.

## Azure DevOps API

### `AzureDevOpsError: ... -> 401`
Authentication failed. The PAT is wrong, expired, or for the wrong
organization/collection. Recreate it and update `.env`.

### `AzureDevOpsError: ... -> 403`
Authenticated but not authorized. The PAT identity lacks permission for the
action — e.g. trying to post comments/vote/merge with a **Read-only** PAT, or the
reviewer account lacks access to the repo. Upgrade the scope to
**Code (Read & Write)** and ensure the account can review the repo.

### `AzureDevOpsError: ... -> 404`
Wrong `base_url`, `project`, or `repository`. For on-prem, confirm the URL
includes the collection (e.g. `.../DefaultCollection`). Project/repo names are
case-sensitive in some setups.

### Repeated transient warnings then failure
5xx/429/timeout responses are retried with backoff (`max_retries`). If they keep
failing, the server is overloaded or unreachable. Increase `timeout_seconds`,
check network/VPN, or try later.

### Votes don't appear / "reviewer not found"
`reviewer.reviewer_id` must be the correct identity GUID, and that identity must
be a permitted reviewer on the repo. Re-derive the GUID from
`/_apis/connectionData` (see [03-azure-devops-pat.md](03-azure-devops-pat.md)).

### Inline comments don't anchor
Inline anchoring can fail if the file/line isn't part of the diff view. The agent
tolerates this: the issue still appears in the summary comment. Check logs for
`Inline comment failed for ...; kept in summary`.

### Build/policy status always "unknown" → never merges
`get_policy_evaluations` needs the project GUID and configured policies. If your
repo has no branch policies, status is `None` (unknown). With
`require_successful_build`/`require_successful_policy` enabled, unknown blocks the
merge by design. Either add policies, or (carefully) relax the `require_*` flags.

## LLM

### `LLM did not return valid JSON` / `LLM JSON failed validation`
The model returned non-JSON or a malformed object. The agent treats this as a
failed review (no approve/merge) and records the error. Usually transient; it
self-corrects on the next commit. If persistent, check the model id and that the
API key is valid.

### Reviews are slow
Large diffs increase latency and cost. Lower `llm.max_diff_chars`, or reduce
`max_changed_files`/`max_diff_lines` so big PRs are skipped for action.

### `anthropic.AuthenticationError`
`ANTHROPIC_API_KEY` is missing or invalid.

## Behavior

### "It reviewed but did nothing"
Expected when `dry_run: true` (default) — status will be `dry_run`. Read the
`message`/logs for what it *would* do. To act, climb the ladder in
[09-dry-run-and-enabling.md](09-dry-run-and-enabling.md).

### "It approved but didn't merge"
By design. Either `auto_complete` is off, or a completion gate failed (medium+
issue, low confidence, unknown build, risky path, conflict, disallowed branch).
The dry-run/logs reasons explain which.

### "It keeps skipping a PR"
The current commit was already reviewed (`status: skipped`). Push a new commit,
or call `POST /review/...` which forces a re-review.

## Logging

### I need more detail
Set `LOG_LEVEL=DEBUG` in `.env` and restart. Note: tokens are still redacted.

### I see `***REDACTED***` in logs
That's the redaction filter working as intended — a secret-like string was
scrubbed.

## Install

### `Cannot uninstall PyYAML ... RECORD file not found`
You're installing into a system Python that owns PyYAML. Use a virtualenv (see
[02-installation.md](02-installation.md)).

Next: [12-dockerize.md](12-dockerize.md).
