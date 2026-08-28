# Operations Guide

## Environment Variables

### Database

| Variable          | Description                       | Default                   | Notes                                       |
| ----------------- | --------------------------------- | ------------------------- | ------------------------------------------- |
| `DATABASE_URL`    | Full PostgreSQL connection string | _(built from components)_ | If set, `DB_*` variables are ignored.       |
| `DB_USER`         | PostgreSQL username               | `postgres`                |                                             |
| `DB_PASSWORD`     | PostgreSQL password               | _(required)_              | Must be set if `DATABASE_URL` is not used.  |
| `DB_NAME`         | PostgreSQL database name          | `ytprocessor`             |                                             |
| `DB_HOST`         | PostgreSQL host                   | `localhost`               |                                             |
| `DB_PORT`         | PostgreSQL port                   | `5432`                    |                                             |
| `DB_POOL_SIZE`    | SQLAlchemy pool size              | `10`                      | Worker production override defaults to `3`. |
| `DB_MAX_OVERFLOW` | SQLAlchemy pool overflow          | `5`                       | Worker production override defaults to `2`. |
| `DB_POOL_TIMEOUT` | SQLAlchemy pool wait timeout      | `30`                      | Must be at least `1`.                       |
| `DB_POOL_RECYCLE` | SQLAlchemy pool recycle setting   | `1800`                    | Must be at least `1`.                       |

### Redis

| Variable               | Description                              | Default                   | Notes                                                                                          |
| ---------------------- | ---------------------------------------- | ------------------------- | ---------------------------------------------------------------------------------------------- |
| `REDIS_URL`            | Full Redis connection string             | _(built from components)_ | If set, `REDIS_*` variables are ignored.                                                       |
| `REDIS_HOST`           | Redis host                               | `localhost`               |                                                                                                |
| `REDIS_PORT`           | Redis port                               | `6379`                    |                                                                                                |
| `REDIS_PASSWORD`       | Redis password                           | _(conditional)_           | Only required when Redis AUTH is enabled. Docker Compose interpolates the value when provided. |
| `RATE_LIMIT_REDIS_URL` | Redis URL for shared rate-limit counters | `REDIS_URL` or localhost  | slowapi stores rate-limit counters here so limits are shared across API replicas (issue #120). |

### Application

| Variable                      | Description                                  | Default                 |
| ----------------------------- | -------------------------------------------- | ----------------------- |
| `SECRET_KEY`                  | JWT signing key (min 32 chars, high entropy) | _(required)_            |
| `CORS_ORIGINS`                | Allowed origins, comma-separated             | `http://localhost:3000` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access token expiry                          | `15`                    |
| `REFRESH_TOKEN_EXPIRE_DAYS`   | Refresh token expiry                         | `7`                     |
| `FILE_EXPIRE_HOURS`           | Download link expiry                         | `24`                    |
| `STORAGE_PATH`                | Local storage directory                      | `./storage`             |
| `COOKIE_SECURE`               | Require HTTPS for cookies                    | `True`                  |

### Observability

| Variable                              | Description                    | Default                            |
| ------------------------------------- | ------------------------------ | ---------------------------------- |
| `FEATURE_METRICS_ENABLED`             | Enable `/metrics` endpoint     | `true`                             |
| `FEATURE_TRACING_ENABLED`             | Enable OpenTelemetry tracing   | `true`                             |
| `OTEL_EXPORTER_OTLP_ENDPOINT`         | Generic OTLP endpoint          | `http://localhost:4317`            |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`  | Trace-specific OTLP endpoint   | `http://localhost:4317/v1/traces`  |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | Metrics-specific OTLP endpoint | `http://localhost:4317/v1/metrics` |
| `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT`    | Logs-specific OTLP endpoint    | `http://localhost:4317/v1/logs`    |
| `OTEL_SERVICE_NAME`                   | Service name in traces         | `vooglaadija`                      |
| `SENTRY_DSN`                          | Sentry project DSN             | _(optional)_                       |

### NetData

| Variable              | Description               | Default                     |
| --------------------- | ------------------------- | --------------------------- |
| `NETDATA_CLAIM_TOKEN` | NetData Cloud claim token | _(optional)_                |
| `NETDATA_CLAIM_URL`   | NetData Cloud claim URL   | `https://app.netdata.cloud` |
| `NETDATA_CLAIM_ROOMS` | NetData Cloud room ID(s)  | _(optional)_                |

### Worker

| Variable                      | Description                                                                                    | Default    |
| ----------------------------- | ---------------------------------------------------------------------------------------------- | ---------- |
| `WORKER_GRACE_PERIOD_SECONDS` | Seconds to wait for in-flight jobs on shutdown                                                 | `30`       |
| `CLEANUP_INTERVAL_MINUTES`    | Minutes between stale-job sweeps                                                               | `60`       |
| `WORKER_ID`                   | Worker identifier; derived from the runtime container hostname (unique per replica) when unset | `worker-1` |
| `WORKER_HEALTH_PORT`          | Port for worker health endpoint                                                                | `8082`     |
| `WORKER_CONCURRENCY`          | In-flight downloads per worker (1..32)                                                         | `2`        |
| `WORKER_REPLICAS`             | Worker container replicas                                                                      | `1`        |
| `YT_DLP_WARM_POOL`            | Use long-lived yt-dlp driver processes                                                         | `true`     |
| `YT_DLP_POOL_SIZE`            | Warm driver processes per worker (1..16)                                                       | `2`        |
| `YT_DLP_PREFER_PROGRESSIVE`   | Try a single progressive stream before merged combos                                           | `false`    |

---

## Docker Deployment

Use the v2 plugin syntax (`docker compose`, not `docker-compose`).

**Production** is deployed through Coolify (see
[PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md)): `./deploy/bootstrap.sh` provisions Docker +
Coolify, assigns the domain, issues the wildcard TLS certificate (Cloudflare DNS-01, auto-renewed by
Caddy) and deploys `docker-compose.yml` — the single source of truth for the stack.

**Local development / standalone:**

```bash
cp .env.example .env        # set DB_PASSWORD, REDIS_PASSWORD, SECRET_KEY
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

**Optional API gateway (Traefik, issue #120):**

The stack rate-limits at the application layer with Redis-backed counters shared across API replicas
(`RATE_LIMIT_REDIS_URL`, see Redis table above). To also put a gateway in front of the API for edge
rate limiting, TLS termination, circuit breaking and security headers:

```bash
docker compose -f docker-compose.yml -f docker-compose.gateway.yml up -d
```

The gateway override attaches the `api` service to the `ytprocessor-network` (alongside the default
network used for inter-service traffic) and routes `/api`, `/web`, `/docs` and `/static` to
`ytprocessor-api:8000`. TLS uses Traefik's default self-signed certificate out of the box; for
production, put the existing platform proxy (Coolify/Caddy) in front or add a
`certificatesresolvers` entrypoint to `infra/traefik/dynamic.yml`.

`docker-compose.yml` defines resource limits, health checks, read-only root filesystems, SELinux
labels (`:Z`) and json-file log rotation for every service. `api`/`worker` pull prebuilt GHCR images
(`IMAGE_TAG`, default `latest`); the local override builds them from the `Dockerfile`.

Worker DB pool overrides live in `docker-compose.yml` as `WORKER_DB_POOL_SIZE` (default 3) and
`WORKER_DB_MAX_OVERFLOW` (default 2). The worker runs a bounded concurrency pool
(`WORKER_CONCURRENCY`, default 2) and is sized at 2 CPU / 2 GB to back it. To scale out, set
`WORKER_REPLICAS` (>1) — each replica runs its own pool, so total parallelism is
`WORKER_REPLICAS * WORKER_CONCURRENCY` and total DB connections are
`WORKER_REPLICAS * (pool + overflow)`. The worker has no static `container_name` (removed to allow
replicas); containers are named `vooglaadija-worker-N` and are addressed by the `worker` service
name. There is also no static `WORKER_ID`: each replica derives its id from the container's runtime
hostname, so scaled replicas write distinct `worker:health:*` keys (set `WORKER_ID` explicitly to
override).

The warm pool also bounds effective parallelism: `YT_DLP_POOL_SIZE` may not exceed the yt-dlp
extraction concurrency (`YT_DLP_EXTRACTION_CONCURRENCY`, which defaults to `WORKER_CONCURRENCY`), so
pool slots are never started beyond what the extraction semaphore can check out.

**Monitoring caveat:** the Prometheus `ytprocessor-worker` job in `infra/prometheus/prometheus.yml`
pins a single static `worker:8082` target. With `WORKER_REPLICAS > 1`, Docker's service DNS
round-robins across replicas, so that job scrapes one replica at a time and worker metrics
(QUEUE_DEPTH, JOBS_COMPLETED, health state) flicker or merge across replicas. For per-replica worker
metrics, add explicit per-replica targets (e.g. DNS service discovery) to the scrape config.

yt-dlp runs through a **warm subprocess pool** (`YT_DLP_WARM_POOL=true`, `YT_DLP_POOL_SIZE=2`):
instead of spawning `python -c "import yt_dlp"` for every job (hundreds of ms of interpreter +
import cold start per call), a small pool of long-lived Python processes imports yt_dlp once and is
fed jobs over stdin. The pool reuses the same process-group kill/orphan-walk semantics as the
per-job path and falls back to it transparently if a driver dies or the pool is disabled. Set
`YT_DLP_WARM_POOL=false` to force the legacy per-job subprocess path.

Configuration validation runs when `core.config.Settings` is constructed outside `TESTING=1`.
Malformed `CORS_ORIGINS`, out-of-range DB or Redis ports, unwritable `STORAGE_PATH`, weak
`SECRET_KEY`, and invalid DB pool values fail startup before the API or worker handles traffic.
`CORS_ORIGINS` entries must be origin-only `http` or `https` URLs; paths, credentials, query
strings, fragments, whitespace, malformed ports, and port zero are rejected.

### Logging

Container logs use the built-in `json-file` driver with rotation (`max-size: 10m`, `max-file: 3`) —
no host plugin required. Application logs are one JSON object per line in production, so
`docker compose logs api` output preserves fields such as `timestamp`, `level`, `logger`, `message`,
`service`, `environment`, and request context like `request_id`.

### Services

| Service          | Image                                             | Exposed Port      | Purpose                  |
| ---------------- | ------------------------------------------------- | ----------------- | ------------------------ |
| `api`            | `ghcr.io/tomkabel/vooglaadija:<IMAGE_TAG>`        | `8000` (internal) | FastAPI application      |
| `worker`         | `ghcr.io/tomkabel/vooglaadija:worker-<IMAGE_TAG>` | `8082` (internal) | Background job processor |
| `db`             | `postgres:15-alpine`                              | `5432` (internal) | PostgreSQL               |
| `redis`          | `redis:7-alpine`                                  | `6379` (internal) | Queue and cache          |
| `otel-collector` | `otel/opentelemetry-collector:0.88.0`             | `4317`, `4318`    | Observability collector  |
| `prometheus`     | `prom/prometheus:v2.54.1` (profile `monitoring`)  | `127.0.0.1:9090`  | Metrics scraping         |
| `grafana`        | `grafana/grafana:11.3.0` (profile `monitoring`)   | `127.0.0.1:3000`  | Dashboards               |
| `backup`         | `postgres:15-alpine` (profile `backup`)           | —                 | Daily `pg_dump`          |

In production, Coolify's Caddy proxy (ports 80/443) is the only public entry point; the local
override binds debug ports to loopback.

---

## Troubleshooting

### Worker fails to start

**Symptoms:** Worker container exits immediately or logs connection errors.

**Checks:**

1. Verify `REDIS_PASSWORD` and `DB_PASSWORD` are set in `.env`.
1. Ensure PostgreSQL and Redis containers are healthy: `docker compose ps`.
1. Check worker logs: `docker compose logs worker`.

### Database connection refused

**Symptoms:** API returns `500` or health checks fail.

**Checks:**

1. Confirm `DB_HOST` resolves. In Docker, use `db` (the service name), not `localhost`.
1. Verify the database exists: `docker compose exec db psql -U postgres -d ytprocessor -c "\dt"`.
1. Check that migrations have run: `hatch run db-migrate` (local) or the API entrypoint handles it
   (Docker).

### CORS errors on SSE

**Symptoms:** Browser console shows CORS errors for `/web/downloads/stream`.

**Cause:** `EventSource` with `withCredentials: true` requires an explicit origin in
`Access-Control-Allow-Origin`; wildcard (`*`) is rejected by browsers for credentialed requests.

**Fix:** Add your frontend origin to `CORS_ORIGINS` (e.g., `http://localhost:8000`).

### Secret key validation fails on startup

**Symptoms:** `ValueError: SECRET_KEY has insufficient entropy` or
`SECRET_KEY must be at least 32 characters`.

**Fix:** Generate a secure key:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### Prometheus cannot scrape `/metrics`

**Symptoms:** Prometheus shows target as down or metrics are missing.

**Checks:**

1. Ensure `FEATURE_METRICS_ENABLED=true`.
1. Verify the scrape target URL is reachable from Prometheus (e.g., `http://api:8000/metrics` inside
   the Docker network).
1. Check for firewall or network policies blocking port 8000.

### Download links return 410 Gone

**Symptoms:** `GET /api/v1/downloads/{id}/file` returns `410`.

**Cause:** Files expire after `FILE_EXPIRE_HOURS` (default 24h). The worker deletes expired files
during cleanup.

**Fix:** Re-create the download job or increase `FILE_EXPIRE_HOURS`.

### Stuck jobs in "processing" status

**Symptoms:** Job remains `processing` indefinitely.

**Cause:** Worker crashed or was killed during extraction.

**Fix:** The stale job reaper resets stuck jobs automatically based on `CLEANUP_INTERVAL_MINUTES`.
You can also manually retry via `POST /api/v1/downloads/{id}/retry`.

## Weekly Repo Audit

The repository is governed by executable fitness functions
([docs/ARCHITECTURE-STANDARD.md](ARCHITECTURE-STANDARD.md)). A scheduled workflow
(`.github/workflows/repo-audit.yml`) runs every Monday 06:00 UTC and can be triggered manually via
**Actions → Weekly Repo Audit → Run workflow**.

### What it does

1. **AUTO-BOT cleanup PR** (`chore/auto-bot/weekly-cleanup`): applies safe fixes only (unused
   imports/variables via `ruff --fix`, formatting). Review and merge; the branch is force-updated
   weekly, so do not rebase it.
2. **Deep scan report**: runs the unit suite (coverage), then measures gates (boundary, banned
   imports, dead code, unused deps, lockfile, secrets) and behavioral measures (complexity, hotspots
   = complexity × churn, temporal coupling, defensive-code density, duplication). The report updates
   a single `repo-audit` issue; the full JSON is in workflow artifacts.

### Running locally

```bash
hatch run audit:quick      # shift-left: ruff + deptry (fast)
hatch run audit:fix        # apply safe fixes, print changed-file manifest
hatch run audit:weekly     # full deep scan (writes ./audit-report.md + .json)
hatch run audit:complexity # strict complexity pass (advisory thresholds)
hatch run audit:dead-code  # vulture (>=80% confidence)
hatch run audit:boundary   # zone-aware import boundary verifier
```

### Baseline and trends

`.github/audit-baseline.json` holds the accepted measurements. Regenerate only when a cleanup lands
and the new state is intentional:

```bash
hatch run audit:weekly -- --baseline   # (or: python scripts/audit_report.py --baseline)
```

Commit the updated baseline in the same PR as the cleanup — the weekly report shows deltas vs this
file, so it must change only via maintainer PRs.

### Triage (in priority order)

1. Gate findings (top of the issue) — fix or the next PR CI run fails.
2. Hotspots with low coverage — refactor behind a stable facade (Strangler Fig protocol), never
   arbitrary file splitting.
3. Temporal-coupling pairs — investigate shared concepts implemented twice.
4. Duplication clones — apply Rule of Three: refactor at the 3rd instance; alembic migration
   boilerplate is exempt.
5. Defensive-code density leaders — define errors out of existence with uniform result objects at
   service boundaries.
