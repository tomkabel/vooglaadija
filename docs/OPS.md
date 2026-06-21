# Operations Guide

## Environment Variables

### Database

| Variable       | Description                       | Default                   | Notes                                      |
| -------------- | --------------------------------- | ------------------------- | ------------------------------------------ |
| `DATABASE_URL` | Full PostgreSQL connection string | _(built from components)_ | If set, `DB_*` variables are ignored.      |
| `DB_USER`      | PostgreSQL username               | `postgres`                |                                            |
| `DB_PASSWORD`  | PostgreSQL password               | _(required)_              | Must be set if `DATABASE_URL` is not used. |
| `DB_NAME`      | PostgreSQL database name          | `ytprocessor`             |                                            |
| `DB_HOST`      | PostgreSQL host                   | `localhost`               |                                            |
| `DB_PORT`      | PostgreSQL port                   | `5432`                    |                                            |

### Redis

| Variable         | Description                  | Default                   | Notes                                                                                          |
| ---------------- | ---------------------------- | ------------------------- | ---------------------------------------------------------------------------------------------- |
| `REDIS_URL`      | Full Redis connection string | _(built from components)_ | If set, `REDIS_*` variables are ignored.                                                       |
| `REDIS_HOST`     | Redis host                   | `localhost`               |                                                                                                |
| `REDIS_PORT`     | Redis port                   | `6379`                    |                                                                                                |
| `REDIS_PASSWORD` | Redis password               | _(conditional)_           | Only required when Redis AUTH is enabled. Docker Compose interpolates the value when provided. |

### Application

| Variable                      | Description                                  | Default                 |
| ----------------------------- | -------------------------------------------- | ----------------------- |
| `SECRET_KEY`                  | JWT signing key (min 32 chars, high entropy) | _(required)_            |
| `CORS_ORIGINS`                | Allowed origins, comma-separated             | `http://localhost:3000` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access token expiry                          | `15`                    |
| `REFRESH_TOKEN_EXPIRE_DAYS`   | Refresh token expiry                         | `7`                     |
| `FILE_EXPIRE_HOURS`           | Download link expiry                         | `24`                    |
| `STORAGE_PATH`                | Local storage directory                      | `./storage`             |
| `COOKIE_SECURE`               | Require HTTPS for cookies                    | `False`                 |

### Observability

| Variable                              | Description                    | Default                            |
| ------------------------------------- | ------------------------------ | ---------------------------------- |
| `FEATURE_METRICS_ENABLED`             | Enable `/metrics` endpoint     | `true`                             |
| `FEATURE_TRACING_ENABLED`             | Enable OpenTelemetry tracing   | `false`                            |
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
| `NETDATA_CLAIM_ROOM`  | NetData Cloud room ID     | _(optional)_                |

### Worker

| Variable                      | Description                                    | Default    |
| ----------------------------- | ---------------------------------------------- | ---------- |
| `WORKER_GRACE_PERIOD_SECONDS` | Seconds to wait for in-flight jobs on shutdown | `30`       |
| `CLEANUP_INTERVAL_MINUTES`    | Minutes between stale-job sweeps               | `60`       |
| `WORKER_ID`                   | Worker identifier (used in logs/metrics)       | `worker-1` |
| `WORKER_HEALTH_PORT`          | Port for worker health endpoint                | `8082`     |

---

## Docker Deployment

Use the v2 plugin syntax (`docker compose`, not `docker-compose`):

```bash
docker compose up -d
```

The compose file includes resource limits, health checks, read-only root filesystems, and SELinux
labels (`:Z`).

### Services

| Service          | Image                                     | Exposed Port   | Purpose                  |
| ---------------- | ----------------------------------------- | -------------- | ------------------------ |
| `api`            | Build from `Dockerfile` (target `api`)    | `8000`         | FastAPI application      |
| `worker`         | Build from `Dockerfile` (target `worker`) | `8082`         | Background job processor |
| `db`             | `postgres:15-alpine`                      | `5432`         | PostgreSQL               |
| `redis`          | `redis:7-alpine`                          | `6379`         | Queue and cache          |
| `nginx`          | `nginx:alpine`                            | `80`, `443`    | Reverse proxy            |
| `swagger-ui`     | `swaggerapi/swagger-ui:v5.1.0`            | `8081`         | API documentation        |
| `otel-collector` | `otel/opentelemetry-collector:0.88.0`     | `4317`, `4318` | Observability collector  |

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
