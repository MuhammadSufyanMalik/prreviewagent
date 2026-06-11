# 2. Installation

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.11+ | `python3 --version` to check |
| pip | Comes with Python |
| Git | To clone the repo |
| Azure DevOps access | Server (on-prem) or Services, with a repo you can review |
| Anthropic API key | For the LLM reviewer |

## Step 1 — Get the code

```bash
git clone https://<your-host>/MuhammadSufyanMalik/prreviewagent.git
cd prreviewagent
```

## Step 2 — Create a virtual environment

A virtualenv keeps these dependencies isolated from your system Python. This is
strongly recommended (on some systems, installing into the system Python fails
because of OS-managed packages like PyYAML).

```bash
python3 -m venv .venv
```

Activate it:

```bash
# Linux / macOS
source .venv/bin/activate

# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

Your shell prompt should now show `(.venv)`.

## Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

This installs:

| Package | Why |
|---------|-----|
| `fastapi`, `uvicorn` | Web framework + server |
| `apscheduler` | Background interval scheduler |
| `sqlalchemy` | SQLite ORM |
| `pydantic`, `pydantic-settings` | Config models + `.env` loading |
| `pyyaml` | Reads `targets.yaml` |
| `httpx` | HTTP client for the Azure DevOps REST API |
| `tenacity` | Retries with backoff for transient API failures |
| `anthropic` | LLM client |
| `pytest` | Test runner |

## Step 4 — Verify the install

Run the test suite — it needs no network, PAT, or API key:

```bash
pytest -q
```

You should see all tests pass (currently **34 passed**). If they do, the code is
wired up correctly and you can move on to configuration.

## Step 5 — Create your env and config files

```bash
cp .env.example .env
```

Then edit `.env` and `config/targets.yaml`. Both are covered in detail in
[04-configuration.md](04-configuration.md), but first you need a PAT — see
[03-azure-devops-pat.md](03-azure-devops-pat.md).

## Troubleshooting the install

- **`ERROR: Cannot uninstall PyYAML ... RECORD file not found`** — you're
  installing into a system Python that owns PyYAML. Use a virtualenv (Step 2).
- **`command not found: python3`** — install Python 3.11+ first.
- **SSL / proxy errors during `pip install`** — you're likely behind a corporate
  proxy; configure `pip` with your proxy or use an internal package mirror.

Next: [03-azure-devops-pat.md](03-azure-devops-pat.md).
