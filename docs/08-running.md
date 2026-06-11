# 8. Running the agent

## Start the server

With the virtualenv active and `.env` + `targets.yaml` filled in:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Add `--reload` during development to auto-restart on code changes:

```bash
uvicorn app.main:app --reload --port 8000
```

On startup you'll see logs like:

```
... | INFO | app.main | Starting PR Review Agent
... | INFO | app.db | Database initialised at data/reviews.db
... | INFO | app.scheduler | Scheduler started: scanning every 300 seconds
```

If the PAT or API key is empty, you'll get warnings (not crashes):

```
... | WARNING | app.main | Azure DevOps PAT is empty; API calls will fail until set
... | WARNING | app.main | LLM API key is empty; reviews will fail until set
```

## What happens automatically

If `scheduler.enabled: true`, the agent scans every enabled target every
`interval_seconds`. Each scan:
- lists active PRs (filtered to each target's `target_branches`),
- reviews any PR whose latest commit hasn't been reviewed yet,
- acts according to your policy (comment/approve/complete, or just dry-run).

The job never overlaps itself; a slow scan won't stack up.

## The API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Liveness probe. Returns `{"status":"ok"}`. |
| `GET` | `/status` | Scheduler state + the current policy flags. |
| `GET` | `/targets` | The configured targets. |
| `GET` | `/reviews?limit=50` | Recent review records from SQLite. |
| `POST` | `/scan` | Trigger a full scan of all enabled targets now (synchronous). |
| `POST` | `/review/{project}/{repository}/{pull_request_id}` | Review one PR, honouring config flags. Uses `force=True`. |
| `POST` | `/dry-run/{project}/{repository}/{pull_request_id}` | Review one PR in forced dry-run (no writes). |

### Examples

Check status:

```bash
curl -s http://localhost:8000/status | python -m json.tool
```

```json
{
  "scheduler_running": true,
  "interval_seconds": 300,
  "dry_run": true,
  "auto_comment": true,
  "auto_approve": false,
  "auto_complete": false,
  "enabled_targets": 2,
  "llm_model": "claude-sonnet-4-6"
}
```

Safely preview a single PR (never writes):

```bash
curl -s -X POST http://localhost:8000/dry-run/ProjectA/RepoA/123 | python -m json.tool
```

```json
{
  "project": "ProjectA",
  "repository": "RepoA",
  "pull_request_id": 123,
  "status": "dry_run",
  "decision": "approve",
  "confidence": 0.94,
  "risk_level": "low",
  "commented": false,
  "approved": false,
  "completed": false,
  "message": "[DRY-RUN] would comment=True approve=False (vote=10) complete=False; reasons: ..."
}
```

Review honouring config (may act if you've enabled actions):

```bash
curl -s -X POST http://localhost:8000/review/ProjectA/RepoA/123
```

Trigger a full scan:

```bash
curl -s -X POST http://localhost:8000/scan
```

List recent reviews:

```bash
curl -s "http://localhost:8000/reviews?limit=20" | python -m json.tool
```

## Interactive docs

FastAPI serves auto-generated docs:

- Swagger UI: <http://localhost:8000/docs>
- ReDoc: <http://localhost:8000/redoc>

## Stopping

`Ctrl+C`. The lifespan handler shuts the scheduler down cleanly and closes the
HTTP client.

## Running it as a long-lived local process

For "always on" on your machine without Docker, run it under a process manager:

- **systemd** (Linux): a small unit that runs the uvicorn command with your env.
- **pm2** / **supervisor**: point them at the uvicorn command.
- A **tmux/screen** session for quick experiments.

(Containerizing is covered in [12-dockerize.md](12-dockerize.md).)

Next: [09-dry-run-and-enabling.md](09-dry-run-and-enabling.md).
