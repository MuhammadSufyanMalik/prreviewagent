# PR Review Agent — Documentation

Detailed, step-by-step documentation for the PR Review Agent. The top-level
[`README.md`](../README.md) is the quick-start; these files go deeper, one topic
per file.

Read them in order the first time:

| # | File | What it covers |
|---|------|----------------|
| 1 | [01-overview.md](01-overview.md) | What the agent is, the big picture, the safety philosophy |
| 2 | [02-installation.md](02-installation.md) | Prerequisites, virtualenv, installing dependencies |
| 3 | [03-azure-devops-pat.md](03-azure-devops-pat.md) | Creating the PAT, required scopes, finding your reviewer GUID |
| 4 | [04-configuration.md](04-configuration.md) | Every field in `.env` and `targets.yaml`, explained |
| 5 | [05-architecture.md](05-architecture.md) | Module map, how the pieces fit together |
| 6 | [06-workflow.md](06-workflow.md) | The review pipeline, step by step, from scan to merge |
| 7 | [07-decision-policy.md](07-decision-policy.md) | The deterministic safety gate, every rule explained |
| 8 | [08-running.md](08-running.md) | Running locally, the scheduler, the API endpoints |
| 9 | [09-dry-run-and-enabling.md](09-dry-run-and-enabling.md) | Dry-run, then safely enabling approve and complete |
| 10 | [10-database.md](10-database.md) | The SQLite schema and how state is used |
| 11 | [11-troubleshooting.md](11-troubleshooting.md) | Common errors and how to fix them |
| 12 | [12-dockerize.md](12-dockerize.md) | Containerizing the agent later |
| 13 | [13-testing.md](13-testing.md) | Running and understanding the test suite |

> ⚠️ **Safety first.** This agent can approve and merge code. It ships in a
> fully passive state (`dry_run: true`, `auto_approve: false`,
> `auto_complete: false`). Read [09-dry-run-and-enabling.md](09-dry-run-and-enabling.md)
> before changing any of those.
