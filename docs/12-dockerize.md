# 12. Dockerizing (later)

v1 is intentionally local-first. When you're ready to containerize, here's a
complete, working setup. Nothing in the app needs to change — it already reads
config from env + a mounted YAML and stores state in a mounted volume.

## Dockerfile

Create `Dockerfile` in the repo root:

```dockerfile
FROM python:3.11-slim

# Avoid .pyc files and buffer issues
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app

# Install deps first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code and default config
COPY app ./app
COPY config ./config

# State lives on a volume so it survives container restarts
ENV CONFIG_PATH=config/targets.yaml \
    DATABASE_PATH=/data/reviews.db \
    LOG_LEVEL=INFO
VOLUME ["/data"]

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## .dockerignore

```
.venv/
data/
.env
__pycache__/
*.pyc
.pytest_cache/
.git/
```

(You don't want your local DB or `.env` baked into the image.)

## Build

```bash
docker build -t pr-review-agent .
```

## Run

Inject secrets as env vars; mount a volume for the DB; optionally mount your own
config:

```bash
docker run -d --name pr-review-agent \
  -p 8000:8000 \
  -e AZURE_DEVOPS_PAT="$AZURE_DEVOPS_PAT" \
  -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  -v "$(pwd)/data:/data" \
  -v "$(pwd)/config/targets.yaml:/app/config/targets.yaml:ro" \
  pr-review-agent
```

Check it:

```bash
curl -s http://localhost:8000/health
docker logs -f pr-review-agent
```

## docker-compose (optional)

`docker-compose.yml`:

```yaml
services:
  pr-review-agent:
    build: .
    ports:
      - "8000:8000"
    environment:
      AZURE_DEVOPS_PAT: ${AZURE_DEVOPS_PAT}
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      LOG_LEVEL: INFO
    volumes:
      - ./data:/data
      - ./config/targets.yaml:/app/config/targets.yaml:ro
    restart: unless-stopped
```

Then:

```bash
# put AZURE_DEVOPS_PAT / ANTHROPIC_API_KEY in your shell or a .env compose reads
docker compose up -d --build
docker compose logs -f
```

## Notes for server deployment

- **Secrets**: prefer your platform's secret store (Docker/K8s secrets, a vault)
  over plain env where possible.
- **Persistence**: the `/data` volume holds the SQLite DB — back it up.
- **Timezone**: the scheduler uses UTC internally; no host TZ needed.
- **Scaling**: run a **single** instance. Multiple instances would scan the same
  PRs concurrently. The DB dedupe reduces—but isn't designed to fully coordinate—
  multi-instance races. Keep it to one.
- **Health checks**: point your orchestrator at `GET /health`.

## Kubernetes sketch (very optional)

A Deployment with one replica, env from a `Secret`, the YAML from a `ConfigMap`,
and a `PersistentVolumeClaim` mounted at `/data`. Liveness/readiness probes on
`/health`. (Out of scope for v1; the building blocks above translate directly.)

Next: [13-testing.md](13-testing.md).
