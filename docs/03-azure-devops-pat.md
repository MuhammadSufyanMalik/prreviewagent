# 3. Azure DevOps PAT & reviewer identity

The agent authenticates to Azure DevOps with a **Personal Access Token (PAT)**.
The PAT identity is also the user whose **review vote** gets cast — so the PAT
should belong to the account you want to appear as the reviewer (often a service
/ bot account).

## Step 1 — Create the PAT

1. Sign in to Azure DevOps (Server or Services).
2. Top-right **avatar → Personal access tokens** (Server: **User settings →
   Personal access tokens**).
3. Click **+ New Token**.
4. Fill in:
   - **Name**: e.g. `pr-review-agent`
   - **Organization**: the one containing your projects (Services only)
   - **Expiration**: set a reasonable lifetime; rotate before it expires
5. Choose **Scopes** (see below).
6. Click **Create**.
7. **Copy the token immediately** — GitHub-style, you only see it once.

## Step 2 — Required scopes

| What the agent does | Scope needed |
|---------------------|--------------|
| List PRs, read iterations, files, diffs, threads | **Code → Read** |
| Post comments, cast reviewer votes, complete/merge | **Code → Read & Write** |
| Read build/branch policy status | **Code → Read** (add **Build → Read** if you use build-validation policies) |

**Minimum for full functionality:** `Code (Read & Write)`.
**For read-only dry-run evaluation:** `Code (Read)` is enough — the agent simply
can't comment/vote/merge, which is fine while you're only observing.

> Prefer the **least privilege** that fits your stage. Start with `Code (Read)`
> for dry-run, upgrade to `Read & Write` only when you enable real actions.

## Step 3 — Put the PAT in `.env`

```env
AZURE_DEVOPS_PAT=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

The YAML never stores the token — it only names the env var:

```yaml
azure_devops:
  pat_env: "AZURE_DEVOPS_PAT"   # the NAME of the env var, not the value
```

The agent resolves `pat_env` against your environment at runtime
(`app/config.py` → `Settings.azure_pat`).

## Step 4 — Find your reviewer GUID

To cast a vote, the agent needs the reviewer's **identity GUID**, set as
`reviewer.reviewer_id` in `targets.yaml`. The simplest way to find the GUID of
the PAT's own identity:

```bash
curl -u :$AZURE_DEVOPS_PAT \
  "https://dev.azure.company.local/DefaultCollection/_apis/connectionData?api-version=7.1"
```

In the JSON response, look for:

```json
{ "authenticatedUser": { "id": "abcdef12-3456-...-...", "displayName": "PR Bot" } }
```

Use that `id` as your `reviewer_id`:

```yaml
reviewer:
  reviewer_id: "abcdef12-3456-...-..."
```

> The reviewer must be allowed to vote on the target repos. If you use a bot
> account, make sure it has at least **Contributor** access to those repos and is
> permitted as a reviewer by your branch policies.

## Security notes

- Never commit `.env` (it's in `.gitignore`).
- The agent **redacts** token-like strings from logs
  (`app/logging_config.py`), but still avoid pasting tokens into shared terminals.
- Rotate the PAT periodically and immediately if it leaks.

Next: [04-configuration.md](04-configuration.md).
