# Architecture — Infrastructure

**Part:** Infrastructure (`infra/`) **Type:** Docker Compose + standalone Caddy deployment

---

## Technology Stack

| Category      | Technology                          | Notes                                     |
| ------------- | ----------------------------------- | ----------------------------------------- |
| Container     | Docker + BuildKit                   | Multi-arch (amd64 + arm64)                |
| Orchestration | Docker Compose                      | Single compose file + optional profiles   |
| Registry      | GitHub Container Registry (ghcr.io) | Images built in CI (optional; local builds supported) |
| Reverse Proxy | Caddy (standalone, port 80/443)     | TLS origin cert for Cloudflare, HTTP/3    |
| TLS           | Self-signed origin cert via Caddy   | Served to Cloudflare (SSL/TLS mode Full)  |
| Monitoring    | Prometheus + Grafana                | Optional `monitoring` profile             |
| Backups       | pg_dump cron (alpine)               | Optional `backup` profile                 |
| Logging       | Docker `json-file` + rotation       | No host plugin required                   |

## Compose Files

| File                        | Environment       | Services                                                                                 |
| --------------------------- | ----------------- | ---------------------------------------------------------------------------------------- |
| `docker-compose.yml`        | Production + dev  | api, worker, db, redis, storage-init, otel-collector (+ `monitoring`, `backup` profiles) |
| `docker-compose.local.yml`  | Local development | Build targets + loopback debug ports                                                     |
| `docker-compose.caddy.yml`  | Production       | Standalone Caddy reverse proxy (ports 80/443) — the only public entry point              |

`docker-compose.production.yml`, `docker-compose.demo.yml`, `docker-compose.monitoring.yml`,
`docker-compose.test.yml` and the nginx/certbot infrastructure were removed — optional services are
now `profiles` in the single compose file.

## Deployment Flow

```text
git push → main
   │
   └─ GitHub Actions: tests → docker.yml builds api+worker (multi-arch, SHA tags) → GHCR
        (optional — the server can also build locally from the checkout)

server (standalone, no Coolify)
   └─ deploy/bootstrap.sh: Docker → .env secrets → Caddyfile + origin cert → compose up
```

Bootstrap (`deploy/bootstrap.sh`) provisions Docker on any VPS, generates production secrets into
`./.env`, writes the `Caddyfile` + self-signed origin certificate for the domain and brings up the
stack behind a standalone Caddy reverse proxy (the only public entry point on ports 80/443).

## Dockerfile

Multi-stage build:

1. **Builder stage** — Install Python deps, build Tailwind CSS
2. **Runtime stage** — Non-root user (1000:1000), `cap_drop: [ALL]`, `read_only: true`,
   `no-new-privileges=true`

Image tags: commit SHA + version tag (+ `latest` for auto-deploys).

## Security

- Non-root container user
- Dropped Linux capabilities
- Read-only root filesystem
- No new privileges
- Health checks on all services
- Resource limits configured
- Secrets stored in `./.env` (mode 600), never committed to version control
