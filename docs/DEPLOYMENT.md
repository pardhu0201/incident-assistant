# Deployment

The app ships as a single Docker container (React build baked into FastAPI's static
files), so it deploys to any container host. The recommended free path is Render.

## Option A - Render (recommended, free)

Render's free web-service tier runs the bundled `Dockerfile` at no cost. The service
sleeps after 15 minutes of inactivity and takes ~50 seconds to wake on the next
request - expected behavior for a free-tier portfolio demo.

### 1. Push this repo to GitHub

See the repo root for the exact commands you were handed alongside this file - once
pushed, the repository is public at `github.com/<you>/incident-assistant`.

### 2. Deploy the blueprint

1. Go to <https://dashboard.render.com>, sign in (GitHub login is fine, free).
2. **New** -> **Blueprint**.
3. Connect the `incident-assistant` repository. Render finds `render.yaml` at the
   repo root automatically and proposes one service: `incident-assistant` (Docker,
   free plan, health check at `/api/health`).
4. Click **Apply**. Render builds the Docker image (~3-5 minutes the first time) and
   deploys it.
5. (Optional) Set `ANTHROPIC_API_KEY` under the service's **Environment** tab to
   upgrade from the deterministic demo-mode diagnosis to Claude-generated diagnoses.
   Leave it blank to stay fully free - the app works completely without it.
6. Once the deploy log shows "Your service is live", open the service URL. Confirm
   `/api/health` returns `{"status": "ok", ...}`, then load the UI and click
   **Load sample logs** on the Incidents page to seed a working demo.

### Why SQLite, not managed Postgres

Render's free Postgres now expires 30 days after creation. This app defaults to
SQLite on the container's own disk, which persists for the life of the service and
never expires - so the whole deployment stays free indefinitely. If you want a
longer-lived, non-ephemeral store instead, set `DATABASE_URL` to any Postgres
connection string (e.g. a free [Neon](https://neon.tech) instance) in the service's
environment - no code change is needed; `app/config.py` normalizes
`postgres://` -> `postgresql+psycopg://` automatically.

## Option B - Docker Compose (local, or any Docker host)

```bash
docker compose up --build
```

`docker-compose.yml` also stands up a real `postgres:17-alpine` container, so this is
the easiest way to exercise the app against genuine Postgres rather than SQLite.

## Option C - any container platform

The image is a standard multi-stage Dockerfile with no platform-specific assumptions:

```bash
docker build -t incident-assistant .
docker run -p 7860:7860 -e ANTHROPIC_API_KEY=... incident-assistant
```

It reads `PORT` (defaults to `7860`), binds `0.0.0.0`, and exposes `/api/health` for
health checks - deployable as-is to Fly.io, Railway, a VM, or any other host that runs
a Dockerfile.

## Environment variables

See [.env.example](../.env.example) at the repo root for the full list with defaults
and explanations. Every value has a working default - the app boots and is fully
functional with an empty `.env`.

## Verifying a deployment

```bash
curl https://<your-service>.onrender.com/api/health
curl -X POST https://<your-service>.onrender.com/api/logs/replay-sample
curl -X POST https://<your-service>.onrender.com/api/incidents/1/analyze
```

Then open the service URL in a browser and confirm the Incidents, Approvals, and
Dashboard pages render real data.

## CI

`.github/workflows/ci.yml` runs on every push/PR: backend lint (`ruff`) + `pytest` +
the golden-set eval as a regression gate, frontend typecheck + build, and a Docker
build-and-boot-healthcheck job. A green run on `main` is a reasonable green light to
redeploy.
