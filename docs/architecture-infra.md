# Architecture — Infrastructure

**Part:** Infrastructure (`infra/`) **Type:** Docker Compose + Coolify deployment

---

## Technology Stack

| Category      | Technology                          | Notes                                     |
| ------------- | ----------------------------------- | ----------------------------------------- |
| Container     | Docker + BuildKit                   | Multi-arch (amd64 + arm64)                |
| Orchestration | Docker Compose                      | Single compose file + optional profiles   |
| Registry      | GitHub Container Registry (ghcr.io) | Images built in CI, pulled on the server  |
| PaaS / CD     | Coolify (self-hosted)               | Deploys the compose, webhook auto-deploys |
| Reverse Proxy | Caddy (Coolify's proxy)             | Wildcard TLS (Cloudflare DNS-01), HTTP/3  |
| TLS           | Let's Encrypt via Caddy             | Auto-issue + auto-renew, no certbot/cron  |
| Monitoring    | Prometheus + Grafana                | Optional `monitoring` profile             |
| Backups       | pg_dump cron (alpine)               | Optional `backup` profile                 |
| Logging       | Docker `json-file` + rotation       | No host plugin required                   |

## Compose Files

| File                       | Environment       | Services                                                                                 |
| -------------------------- | ----------------- | ---------------------------------------------------------------------------------------- |
| `docker-compose.yml`       | Production + dev  | api, worker, db, redis, storage-init, otel-collector (+ `monitoring`, `backup` profiles) |
| `docker-compose.local.yml` | Local development | Build targets + loopback debug ports                                                     |

`docker-compose.production.yml`, `docker-compose.demo.yml`, `docker-compose.monitoring.yml`,
`docker-compose.test.yml` and the nginx/certbot infrastructure were removed — optional services are
now `profiles` in the single compose file.

## Deployment Flow

```text
git push → main
   │
   ├─ GitHub Actions: tests → docker.yml builds api+worker (multi-arch, SHA tags) → GHCR
   │
   └─ Coolify webhook: docker compose up -d --pull always (health-gated, rollback via UI)
```

Bootstrap (`deploy/bootstrap.sh`) provisions Docker + Coolify on any VPS, switches Coolify's proxy
to Caddy with the Cloudflare DNS-01 module (wildcard certs, auto-renewal) and creates the
application from the public repository.

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
- Secrets stored encrypted in Coolify, never in the repo
