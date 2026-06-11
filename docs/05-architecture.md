# 5. Architecture

## Module map

```
app/
  main.py              FastAPI app, dependency wiring, lifespan (startup/shutdown)
  config.py            .env + YAML config models and loading
  logging_config.py    structured logging + secret redaction
  db.py                SQLite engine, session scope, data-access helpers
  models.py            SQLAlchemy ORM tables
  azure_devops_client.py   REST client for Azure DevOps
  llm_client.py        LLM provider wrapper (Anthropic)
  review_engine.py     prompt building + strict-JSON parsing + ReviewResult model
  decision_policy.py   deterministic safety gate (PRContext, evaluate, compute_vote)
  pr_service.py        orchestration: scan → review → comment → approve → complete
  scheduler.py         APScheduler background job
  dashboard.py         FastAPI routes
```

## Responsibilities (and what each does *not* do)

### `config.py`
Loads `.env` (`EnvSettings`) and the YAML (`AppConfig`), and resolves secret
env-var **names** into values via the `Settings` bundle. It does **not** read
secrets from YAML. `get_settings()` caches the result for the process.

### `logging_config.py`
Configures a single stdout handler with a consistent line format and a
`RedactionFilter` that scrubs `Authorization:` headers, `pat=`/`token=` patterns,
etc. Nothing else logs secrets, but this is the backstop.

### `models.py` + `db.py`
- `models.py` defines two tables: `reviewed_pull_requests` and `review_actions`
  (see [10-database.md](10-database.md)).
- `db.py` creates the engine, ensures the DB directory/tables exist, and exposes
  `session_scope()` (a transactional context manager) plus small query helpers.

### `azure_devops_client.py`
The **only** module that talks to Azure DevOps. It:
- authenticates with PAT Basic auth,
- retries transient failures (5xx/429/timeouts) with exponential backoff,
- exposes typed methods: list/get PRs, iterations, changed files, content,
  threads, post comment (summary or inline), set reviewer vote, policy
  evaluations, and `complete_pull_request`.

It raises `AzureDevOpsError` for non-retryable failures so callers can react.

### `llm_client.py`
A thin `LLMClient` protocol with an `AnthropicClient` implementation. It takes a
system prompt + user message and returns raw text. It knows nothing about PRs.

### `review_engine.py`
Owns the **review contract**:
- the system prompt that demands strict JSON,
- `build_user_prompt(...)` to assemble PR context + diff,
- `parse_result(...)` that strips code fences, isolates the JSON object, and
  validates it into a `ReviewResult` (with `Issue` items).

If the LLM returns invalid JSON, parsing raises — the orchestration treats that
as a failed review (no approve/merge).

### `decision_policy.py`
Pure, deterministic, **no I/O**. Given a `ReviewResult`, a `PRContext` (objective
facts), and the `ReviewPolicy` config, it returns a `PolicyDecision`:
`should_comment`, `should_approve`, `should_complete`, the `vote`, plus
human-readable `reasons` and `blockers`. This is the safety brain and the most
heavily unit-tested module. Details: [07-decision-policy.md](07-decision-policy.md).

### `pr_service.py`
The orchestrator. For each PR it gathers facts via the client, runs the engine,
asks the policy what's allowed, and then executes actions in the mandated order,
recording everything to SQLite. It also re-checks hard preconditions right before
merging (conflicts, build/policy) as a second line of defense.

### `scheduler.py`
Wraps APScheduler to call `PRService.scan_all()` every `interval_seconds`. The
job is non-overlapping (`max_instances=1`, `coalesce=True`).

### `dashboard.py` + `main.py`
`main.py` builds the dependency graph in a FastAPI `lifespan`: load settings,
init DB, build the client/engine/service/scheduler, stash them on `app.state`,
start the scheduler, and clean up on shutdown. `dashboard.py` defines the HTTP
routes that read from `app.state`.

## Data flow for one PR

```
get_pull_request ─┐
get_latest_commit ─┤
get_changed_files ─┤
get_file_content  ─┼─► PRContext + diff text + existing comments
get_threads       ─┤
get_policy_eval   ─┘
                       │
                       ▼
            ReviewEngine.review()  ──►  ReviewResult (strict JSON)
                       │
                       ▼
         decision_policy.evaluate()  ──►  PolicyDecision
                       │
        ┌──────────────┼───────────────┐
        ▼              ▼                ▼
   post comment   set vote        complete PR
   (if allowed)   (if allowed)    (if allowed)
        │              │                │
        └──────────────┴────────────────┘
                       ▼
            persist to SQLite + audit log
```

## Why the separation matters

- **Testability**: `decision_policy` has no network, so its rules are tested
  exhaustively without mocks. `pr_service` is tested with a fake client + fake
  engine to prove ordering guarantees.
- **Safety**: the LLM's opinion enters only as data into a deterministic gate;
  it can never directly cause a merge.
- **Swappability**: change LLM provider (`llm_client.py`) or add MCP later
  without touching the policy or orchestration logic.

Next: [06-workflow.md](06-workflow.md).
