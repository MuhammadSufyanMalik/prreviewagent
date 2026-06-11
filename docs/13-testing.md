# 13. Testing

The suite needs no network, PAT, or API key — it uses fakes and a temp SQLite
DB, so it's safe and fast.

## Run everything

```bash
source .venv/bin/activate
pytest -q
```

Expected: all tests pass (currently **34 passed**).

## Useful invocations

```bash
pytest -v                              # verbose, per-test names
pytest tests/test_decision_policy.py   # one file
pytest -k "complete"                   # tests matching a keyword
pytest -x                              # stop at first failure
```

## What's covered

### `tests/test_decision_policy.py`
The deterministic safety gate — the most important logic. It verifies:

- **Vote mapping** (`compute_vote`): critical→-10, high→-5, blockers→-5,
  medium→0, low→5, clean→10.
- **Approval gating**: clean PR approves; low confidence blocks; disallowed
  branch blocks; oversize PR blocks; `auto_approve: false` blocks.
- **Completion gating**: clean PR completes; medium/high/critical block;
  "approve with suggestions" (vote 5) does **not** complete; complete-confidence
  threshold enforced; unknown build blocks; failed policy blocks; conflicts
  block; risky paths block; `auto_complete: false` blocks.

These tests are the executable form of [07-decision-policy.md](07-decision-policy.md).

### `tests/test_config.py`
Configuration loading and validation:

- Loads a valid YAML and strips the `base_url` trailing slash.
- `enabled_targets()` filters out disabled targets.
- Default safety flags are correct (`dry_run=True`, `auto_approve=False`,
  `auto_complete=False`).
- Invalid `merge_strategy` is rejected.
- Missing config file raises `FileNotFoundError`.

### `tests/test_pr_service.py`
The orchestration ordering and safety guarantees, using a `FakeADO` client and
`FakeEngine` (no real network/LLM) against a temp DB:

- **Happy path**: comment → approve(10) → complete, all succeed.
- **Comment failure blocks approve and complete** (no vote, no merge).
- **Approval failure blocks complete** (commented, but no merge).
- **Conflicts block complete.**
- **Failing build blocks complete.**
- **Dry-run takes no actions** (no comments/votes/merge).
- **`dry_run=True` parameter overrides config.**
- **Already-reviewed commit is skipped.**
- **Critical issue: comments but never approves.**

## Adding tests

- Put new tests in `tests/`, named `test_*.py`.
- For policy logic, prefer `test_decision_policy.py` — it's pure and needs no
  mocks; use the `make_policy` / `make_ctx` / `make_review` helpers there.
- For orchestration, extend `FakeADO` in `test_pr_service.py` to simulate new
  Azure DevOps behaviors (e.g. set `fail_comment` / `fail_vote`, toggle
  `conflicts` / `build_ok` / `policy_ok`).

## Continuous integration (optional)

A minimal GitHub Actions workflow (`.github/workflows/ci.yml`) would be:

```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt
      - run: pytest -q
```

(Not included in v1; add it when you set up CI.)

---

That's the full documentation set. Back to the [index](README.md).
