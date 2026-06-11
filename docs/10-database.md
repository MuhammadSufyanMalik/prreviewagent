# 10. Database

The agent uses a local **SQLite** file (default `data/reviews.db`, set by
`DATABASE_PATH`). It is created automatically, along with its parent directory,
on startup (`app/db.py` → `init_db`). Models are in `app/models.py`.

## Why a database

Two reasons:

1. **Deduplication** — so the agent doesn't review the same commit every
   interval. The latest commit id is the key.
2. **Audit trail** — a record of every action taken, for transparency and
   debugging.

## Table: `reviewed_pull_requests`

One row per `(project, repository, pull_request_id, latest_commit_id)`. A new
commit on the same PR creates a new row, which is how re-review on new commits
works.

| Column | Type | Meaning |
|--------|------|---------|
| `id` | int (PK) | Row id |
| `project` | str | Azure DevOps project |
| `repository` | str | Repository name |
| `pull_request_id` | int | PR number |
| `source_branch` | str | e.g. `refs/heads/feature/x` |
| `target_branch` | str | e.g. `refs/heads/main` |
| `latest_commit_id` | str | The reviewed commit (dedupe key) |
| `reviewed_at` | datetime | When reviewed (UTC) |
| `decision` | str | The LLM's decision string |
| `confidence` | float | LLM confidence 0–1 |
| `risk_level` | str | low / medium / high / critical |
| `comment_posted` | bool | Did the summary comment post? |
| `approved` | bool | Did the agent cast an approval vote? |
| `completed` | bool | Did the agent merge it? |
| `error_message` | str | Populated when status was `error` |

**Unique constraint:** `(project, repository, pull_request_id, latest_commit_id)`
named `uq_reviewed_pr_commit`. This is what `get_reviewed_for_commit` checks
before reviewing, and what `_persist` upserts against.

## Table: `review_actions`

An append-only log of individual actions.

| Column | Type | Meaning |
|--------|------|---------|
| `id` | int (PK) | Row id |
| `project` | str | Project |
| `repository` | str | Repository |
| `pull_request_id` | int | PR number |
| `action_type` | str | `comment` / `approve` / `complete` / `dry_run` |
| `status` | str | `success` / `failure` / `skipped` / `dry_run` |
| `message` | str | Detail (e.g. `vote=10`, or an error string) |
| `created_at` | datetime | When (UTC) |

## How state is used

- **Before review** (`pr_service.review_pull_request`): looks up
  `reviewed_pull_requests` for the current commit; if present and not `force`,
  returns `skipped`.
- **After each action** (`_log_action`): writes a `review_actions` row.
- **After the review** (`_persist`): upserts the `reviewed_pull_requests` row
  with the final outcome.
- **API** (`GET /reviews`): reads recent `reviewed_pull_requests` rows.

## Inspecting the database

Use the `sqlite3` CLI:

```bash
sqlite3 data/reviews.db
```

```sql
-- recent reviews
SELECT pull_request_id, decision, confidence, comment_posted, approved, completed, reviewed_at
FROM reviewed_pull_requests
ORDER BY reviewed_at DESC
LIMIT 20;

-- action audit log for a PR
SELECT action_type, status, message, created_at
FROM review_actions
WHERE pull_request_id = 123
ORDER BY created_at;
```

## Resetting

To force the agent to re-review everything, stop it and delete the DB file:

```bash
rm data/reviews.db
```

It will be recreated empty on the next start. (Or use the `/review/...` endpoint,
which re-reviews a single PR with `force=True` regardless of stored state.)

## Backups & migrations

- The DB is a single file — back it up by copying it.
- v1 has no migration framework; the schema is created with `create_all`. If you
  change `models.py`, either delete the DB (loses history) or add a migration
  tool (e.g. Alembic) before changing columns in production.

Next: [11-troubleshooting.md](11-troubleshooting.md).
